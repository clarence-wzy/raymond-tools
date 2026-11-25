"""Web API for the local video downloader."""

from __future__ import annotations

import os
import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from . import config, folder_picker, system_proxy
from .core import DownloadJob, JobConfig
from .locator import ToolNotFound, Tools, find
from .network import origin, validate_proxy
from .scheduler import SCHEDULER

ROOT = Path(__file__).resolve().parents[1]
CFG = config.load()
TOOLS: Tools | None = None
TOOLS_ERROR = ""
JOBS: dict[str, DownloadJob] = {}
LOCK = threading.RLock()
MAX_BATCH = 50
SCHEDULER.configure(CFG["max_concurrent"])
INDEX_PATH = ROOT / ".task-index.json"


def _load_index() -> dict:
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


SOURCE_INDEX = _load_index()


def _source_key(url: str, directory: Path) -> str:
    return hashlib.sha256((str(directory.resolve()) + "\0" + url).encode()).hexdigest()


def _remember(job: DownloadJob) -> None:
    with LOCK:
        entry = {"output": str(job.cfg.output.resolve())}
        if job.progress.status == "done" and job.cfg.output.is_file():
            stat = job.cfg.output.stat()
            entry.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns, duration=job.progress.total_time)
        key = _source_key(job.cfg.url, job.cfg.output.parent)
        fd, temporary = tempfile.mkstemp(prefix=".task-index.", dir=str(INDEX_PATH.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({**SOURCE_INDEX, key: entry}, stream, ensure_ascii=False)
            os.replace(temporary, INDEX_PATH)
            SOURCE_INDEX[key] = entry
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _completed_file_unchanged(output: Path, entry: dict) -> bool:
    try:
        stat = output.stat()
        return (
            output.is_file()
            and Path(entry.get("output", "")) == output.resolve()
            and stat.st_size > 0
            and stat.st_size == entry.get("size")
            and stat.st_mtime_ns == entry.get("mtime_ns")
        )
    except OSError:
        return False


def _enqueue(job: DownloadJob) -> None:
    try:
        _remember(job)
    except OSError as error:
        raise ValueError("无法保存任务索引，请检查程序目录的写入权限。") from error
    job.on_finish = lambda: _remember(job)
    job.start()
    JOBS[job.id] = job


def _video_url(value: str) -> str:
    if not value or len(value) > 8192 or any(char.isspace() for char in value):
        raise ValueError("请填写有效的视频地址")
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        if not parsed.hostname or parsed.port == 0:
            raise ValueError("视频地址缺少有效的主机或端口")
    elif not (CFG.get("allow_local_input") and parsed.scheme == "file" and parsed.path):
        raise ValueError("只支持 http/https 地址（本地文件需在 config.json 开启 allow_local_input）")
    return value


def _restart_job(
    old: DownloadJob, request: dict | None = None, *, restart: bool = False,
) -> DownloadJob:
    if old.is_running():
        return old
    output = old.cfg.output
    if _output_in_use(output):
        raise ValueError("已有任务正在写入同一个文件")
    if not restart and (output.exists() or output.is_symlink()):
        raise ValueError("输出文件已存在，移走同名文件后可继续重试。")
    cfg = replace(old.cfg, restart=restart, strict_errors=CFG["strict_errors"], hls_workers=CFG["hls_workers"], native_hls=CFG["native_hls"])
    if request is not None:
        url = _video_url(str(request.get("url", cfg.url) or "").strip())
        headers = str(request.get("headers", cfg.headers) or "")
        if len(headers) > 16384:
            raise ValueError("请求头过长")
        if url != cfg.url and headers.strip() and (
            not urlparse(url).hostname or not urlparse(cfg.url).hostname or origin(url) != origin(cfg.url)
        ):
            raise ValueError("新地址的来源不同。请清除请求头，或作为新任务添加并填写新来源的请求头。")
        cache = output.with_name(output.name + ".parts")
        if url != cfg.url and not restart and any(cache.glob("part_*.ts")):
            raise ValueError("现有分段按原地址生成。请使用原地址继续，或将新地址作为新任务下载。")
        cfg = replace(cfg, url=url, headers=headers, proxy=str(request.get("proxy", cfg.proxy) or "").strip())
    job = DownloadJob(cfg, old.ffmpeg, old.ffprobe)
    job.id = old.id
    job.logs.extend(old.logs)
    _enqueue(job)
    return job


def refresh_tools(hint: str | None = None) -> Tools | None:
    global TOOLS, TOOLS_ERROR
    try:
        TOOLS = find(hint or CFG.get("ffmpeg_path") or None)
        TOOLS_ERROR = ""
    except ToolNotFound as error:
        TOOLS = None
        TOOLS_ERROR = str(error)
    return TOOLS


def tools_payload() -> dict | None:
    if TOOLS is None:
        return None
    return {
        "ffmpeg": str(TOOLS.ffmpeg),
        "ffprobe": str(TOOLS.ffprobe),
        "version": TOOLS.version,
    }


def safe_filename(name: str, url: str) -> str:
    base = (name or "").strip()
    if not base:
        base = Path(urlparse(url).path).name or ""
        if not base or "." not in base:
            base = time.strftime("video_%Y%m%d_%H%M%S")
    if base.lower().endswith((".m3u8", ".m3u", ".mpd")):
        base = Path(base).stem + ".mp4"
    base = Path(base).name
    for char in '<>:"/\\|?*':
        base = base.replace(char, "_")
    if base in ("", ".", ".."):
        base = time.strftime("video_%Y%m%d_%H%M%S") + ".mp4"
    if Path(base).suffix.lower() not in (".mp4", ".mkv", ".mov", ".m4v"):
        base += ".mp4"
    return base


def _get_job(payload: dict) -> DownloadJob:
    job_id = str(payload.get("id") or "").strip()
    if not job_id:
        raise ValueError("缺少任务 id")
    job = JOBS.get(job_id)
    if job is None:
        raise ValueError("任务不存在或已被移除")
    return job


def _output_in_use(output: Path) -> bool:
    return any(job.cfg.output == output and job.is_running() for job in JOBS.values())


def _taken(output: Path) -> bool:
    if output.exists() or output.is_symlink():
        return True
    if (output.parent / (output.name + ".parts")).exists():
        return True
    return _output_in_use(output)


def _available(output: Path, restart: bool, reserved: set[Path]) -> bool:
    if output in reserved or _output_in_use(output):
        return False
    if restart:
        return True
    return not _taken(output)


def _unique_output(
    out_dir: Path,
    filename: str,
    restart: bool,
    reserved: set[Path],
) -> Path:
    candidate = out_dir / filename
    if _available(candidate, restart, reserved):
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = out_dir / f"{stem}_{index}{suffix}"
        if _available(candidate, restart, reserved):
            return candidate
    raise ValueError("无法生成不冲突的输出文件名")


def _create_job(
    payload: dict,
    url: str,
    *,
    dedupe: bool,
    filename: str = "",
    reserved: set[Path] | None = None,
) -> DownloadJob:
    url = _video_url(str(url or "").strip())

    tools = TOOLS or refresh_tools()
    if tools is None:
        raise ValueError(TOOLS_ERROR or "未找到 ffmpeg")

    raw_dir = str(payload.get("out_dir") or CFG.get("output_dir") or "").strip()
    if not raw_dir:
        raise ValueError("请填写保存目录")
    out_dir = Path(raw_dir).expanduser()
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir = out_dir.resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"无法创建保存目录：{error}") from error

    headers = str(payload.get("headers") or "")
    if len(headers) > 16384:
        raise ValueError("请求头过长")
    proxy = str(payload.get("proxy") or "").strip()
    restart = bool(payload.get("restart"))
    base_name = safe_filename(filename, url)
    reuse_known_cache = False

    with LOCK:
        # Source identity is independent of the automatically chosen file name.
        if not restart:
            previous = next((item for item in reversed(list(JOBS.values())) if item.cfg.url == url and item.cfg.output.parent.resolve() == out_dir.resolve()), None)
            if previous is not None:
                if previous.is_running():
                    return previous
                if previous.progress.status != "done" and not (
                    previous.cfg.output.exists() or previous.cfg.output.is_symlink()
                ):
                    return _restart_job(previous, payload)
                entry = SOURCE_INDEX.get(_source_key(url, out_dir), {})
                if _completed_file_unchanged(previous.cfg.output, entry):
                    return previous
                # Preserve a file that the user changed after completion.
                dedupe = True
            entry = SOURCE_INDEX.get(_source_key(url, out_dir), {})
            known = Path(entry.get("output", ""))
            if known.is_absolute() and known.parent == out_dir.resolve():
                cache = known.parent / (known.name + ".parts")
                if _completed_file_unchanged(known, entry):
                    cfg = JobConfig(url, known, headers=headers, proxy=proxy, trusted_header_origins=CFG["trusted_header_origins"])
                    job = DownloadJob(cfg, tools.ffmpeg, tools.ffprobe)
                    job._set(status="done", percent=100, total_time=entry.get("duration"), out_time=entry.get("duration") or 0, message="此视频已完成，直接使用已有文件")
                    JOBS[job.id] = job
                    return job
                if known.exists() or known.is_symlink():
                    dedupe = True
                elif cache.is_dir():
                    reuse_known_cache = True
                    base_name = known.name
                    dedupe = False
        if dedupe or (not filename and not reuse_known_cache):
            output = _unique_output(out_dir, base_name, restart, reserved or set())
        else:
            output = out_dir / base_name
            if _output_in_use(output):
                raise ValueError("已有任务正在写入同一个文件")
            parts_dir = output.parent / (output.name + ".parts")
            if parts_dir.exists() and not restart and not reuse_known_cache:
                raise ValueError("此文件名已有来源不明的缓存，请从原任务重试或更换文件名。")
            if not restart and (output.exists() or output.is_symlink()):
                raise ValueError(f"文件已存在：{output}，请更换文件名。")

        job_config = JobConfig(
            url=url,
            output=output,
            headers=headers,
            restart=restart,
            safety=float(CFG.get("safety_seconds", 2.0)),
            stall_timeout=float(CFG.get("stall_timeout", 60)),
            max_retries=int(CFG.get("max_retries", 10)),
            keep_parts=bool(CFG.get("keep_parts", False)),
            proxy=proxy,
            strict_errors=CFG["strict_errors"],
            hls_workers=CFG["hls_workers"],
            native_hls=CFG["native_hls"],
            trusted_header_origins=CFG["trusted_header_origins"],
        )
        job = DownloadJob(job_config, tools.ffmpeg, tools.ffprobe)
        _enqueue(job)
    return job


def state(payload: dict | None = None) -> dict:
    if TOOLS is None and not TOOLS_ERROR:
        refresh_tools()
    with LOCK:
        jobs = list(JOBS.values())
    jobs.sort(key=lambda job: (0 if job.is_running() else 1, -(job.started_at or 0)))
    return {
        "tools": tools_payload(),
        "tools_error": TOOLS_ERROR,
        "config": {
            "output_dir": CFG.get("output_dir"),
            "port": CFG.get("port"),
            "stall_timeout": CFG.get("stall_timeout"),
            "allow_local_input": CFG.get("allow_local_input", False),
            **{key: CFG[key] for key in ("max_concurrent", "hls_workers", "strict_errors", "native_hls")},
        },
        "system_proxy": system_proxy.detect(),
        "jobs": [job.snapshot(include_logs=False) for job in jobs],
    }


def start(payload: dict) -> dict:
    url = str(payload.get("url") or "").strip()
    job = _create_job(
        payload,
        url,
        dedupe=False,
        filename=str(payload.get("filename") or ""),
    )
    return {"ok": True, "job": job.snapshot(include_logs=False)}


def start_batch(payload: dict) -> dict:
    raw_urls = payload.get("urls")
    if raw_urls is None and payload.get("url"):
        raw_urls = [payload.get("url")]
    if not isinstance(raw_urls, list) or not raw_urls:
        raise ValueError("请提供至少一个视频地址")
    urls = [str(item).strip() for item in raw_urls]
    urls = [item for item in urls if item]
    if not urls:
        raise ValueError("请填写视频地址")
    if len(urls) > MAX_BATCH:
        raise ValueError(f"一次最多添加 {MAX_BATCH} 个地址")

    filename = str(payload.get("filename") or "").strip()
    filenames = payload.get("filenames")
    if filenames is None:
        filenames = [""] * len(urls)
    if not isinstance(filenames, list) or len(filenames) != len(urls) or any(
        not isinstance(name, str) for name in filenames
    ):
        raise ValueError("文件名列表需要与视频地址一一对应")

    numbered = len(urls) > 1
    jobs: list[dict] = []
    errors: list[dict] = []
    results: list[dict] = []
    reserved: set[Path] = set()
    seen_jobs: set[str] = set()
    seen_urls: set[str] = set()
    with LOCK:
        existing_ids = set(JOBS)
    reused: set[str] = set()
    for index, url in enumerate(urls):
        if url in seen_urls:
            results.append({"index": index, "status": "duplicate"})
            continue
        seen_urls.add(url)
        try:
            name = filenames[index].strip()
            if not name and filename:
                name = filename
                if numbered:
                    base = Path(safe_filename(filename, url))
                    name = f"{base.stem}_{index + 1:02d}{base.suffix}"
            job = _create_job(
                payload,
                url,
                dedupe=True,
                filename=name,
                reserved=reserved,
            )
            reserved.add(job.cfg.output)
            was_reused = job.id in existing_ids or job.progress.status == "done"
            if was_reused:
                reused.add(job.id)
            results.append({"index": index, "status": "reused" if was_reused else "created", "output": job.cfg.output.name})
            if job.id not in seen_jobs:
                jobs.append(job.snapshot(include_logs=False))
                seen_jobs.add(job.id)
        except ValueError as error:
            errors.append({"url": url, "error": str(error)})
            results.append({"index": index, "status": "failed", "error": str(error)})

    if not jobs and payload.get("include_results") is not True:
        raise ValueError(errors[0]["error"] if errors else "没有创建任何任务")
    return {"ok": True, "jobs": jobs, "errors": errors, "reused": len(reused), "results": results}


def cancel(payload: dict) -> dict:
    job = _get_job(payload)
    if not job.is_running():
        raise ValueError("任务已经结束")
    job.cancel()
    return {"ok": True}


def edit_details(payload: dict) -> dict:
    job = _get_job(payload)
    return {"url": job.cfg.url, "output": str(job.cfg.output), "headers": job.cfg.headers, "proxy": job.cfg.proxy}


def retry(payload: dict) -> dict:
    with LOCK:
        old = _get_job(payload)
        restart = payload.get("restart") is True
        changes = payload.get("changes")
        if changes is not None and not isinstance(changes, dict):
            raise ValueError("任务设置无效")
        if old.is_running():
            if restart or changes is not None:
                raise ValueError("任务正在运行，请先取消")
            return {"ok": True, "job": old.snapshot(include_logs=False)}
        if old.progress.status == "done" and not restart:
            entry = SOURCE_INDEX.get(_source_key(old.cfg.url, old.cfg.output.parent), {})
            if not _completed_file_unchanged(old.cfg.output, entry):
                raise ValueError("已完成文件已更改或丢失，请重新添加视频；现有文件不会被覆盖。")
            return {"ok": True, "job": old.snapshot(include_logs=False)}
        if getattr(old, "headers_missing", False) and (changes is None or "headers" not in changes):
            raise ValueError("旧版本未保留该任务的请求头，请使用“编辑后重试”补充请求头。")
        if "settings" in payload:
            if not isinstance(payload["settings"], dict):
                raise ValueError("下载设置无效")
            settings(payload["settings"])
        job = _restart_job(old, changes, restart=restart)
        return {"ok": True, "job": job.snapshot(include_logs=False)}


def cancel_all(payload: dict | None = None) -> dict:
    with LOCK:
        jobs = list(JOBS.values())
    cancelled = 0
    for job in jobs:
        if job.is_running():
            job.cancel()
            cancelled += 1
    return {"ok": True, "cancelled": cancelled}


def remove(payload: dict) -> dict:
    with LOCK:
        job = _get_job(payload)
        if job.is_running():
            raise ValueError("任务正在运行，请先取消")
        JOBS.pop(job.id, None)
    return {"ok": True}


def clear_finished(payload: dict | None = None) -> dict:
    with LOCK:
        completed_only = bool(payload and payload.get("completed_only") is True)
        ids = [
            job_id for job_id, job in JOBS.items()
            if not job.is_running() and (not completed_only or job.progress.status == "done")
        ]
        for job_id in ids:
            JOBS.pop(job_id, None)
    return {"ok": True, "removed": len(ids)}


def logs(payload: dict) -> dict:
    job = _get_job(payload)
    return {"id": job.id, "logs": job.snapshot(include_logs=True).get("logs", [])}


def detect(payload: dict) -> dict:
    hint = str(payload.get("path") or "").strip()
    if payload.get("reset"):
        CFG["ffmpeg_path"] = ""
    tools = refresh_tools(hint or None)
    if tools is None:
        raise ValueError(TOOLS_ERROR)
    CFG["ffmpeg_path"] = str(tools.ffmpeg)
    config.save(CFG)
    return {"ok": True, "tools": tools_payload()}


def settings(payload: dict) -> dict:
    updated = dict(CFG)
    for key, maximum in (("max_concurrent", 6), ("hls_workers", 8)):
        value = payload.get(key, CFG[key])
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{key} 必须是 1 到 {maximum} 的整数")
        updated[key] = value
    for key in ("strict_errors", "native_hls"):
        value = payload.get(key, CFG[key])
        if type(value) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
        updated[key] = value
    with LOCK:
        try:
            config.save(updated)
        except OSError as error:
            raise ValueError("无法保存下载设置，请检查写入权限。") from error
        CFG.update(updated)
        SCHEDULER.configure(CFG["max_concurrent"])
    return {"ok": True}


def test_proxy(payload: dict) -> dict:
    proxy = str(payload.get("proxy") or "").strip()
    if not proxy or proxy == "direct":
        raise ValueError("请选择“指定 HTTP 代理”并填写地址后再测试")
    validate_proxy(proxy)
    parsed = urlparse(proxy)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("代理地址格式应为 http://host:port")
    port = parsed.port or 80
    started = time.perf_counter()
    try:
        with socket.create_connection((parsed.hostname, port), timeout=5):
            pass
    except OSError as error:
        raise ValueError(f"无法连接代理：{error}") from error
    return {"ok": True, "ms": round((time.perf_counter() - started) * 1000)}


def select_folder(payload: dict) -> dict:
    selected = folder_picker.choose(str(payload.get("path") or CFG.get("output_dir") or Path.home()))
    if selected is None:
        return {"ok": True, "cancelled": True}
    with LOCK:
        updated = dict(CFG, output_dir=selected)
        try:
            config.save(updated)
        except OSError as error:
            raise ValueError("无法保存目录设置，请检查程序目录的写入权限。") from error
        CFG.update(updated)
    return {"ok": True, "cancelled": False, "path": selected}


def open_folder(payload: dict) -> dict:
    path = Path(str(payload.get("path") or CFG.get("output_dir") or "")).expanduser()
    if path.is_file():
        path = path.parent
    if not path.exists():
        raise ValueError(f"目录不存在：{path}")
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        raise ValueError(f"无法打开目录：{error}") from error
    return {"ok": True}


def shutdown() -> None:
    with LOCK:
        jobs = list(JOBS.values())
    for job in jobs:
        if job.is_running():
            job.cancel()
