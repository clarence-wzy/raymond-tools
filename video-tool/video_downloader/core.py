from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from .scheduler import SCHEDULER
from .network import legacy_headers, parse_headers, trusted_origin, validate_proxy
from .privacy import redact_text, secret_values

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROXY_ENV_KEYS = (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)


def child_env(proxy: str) -> dict[str, str]:
    env = os.environ.copy()
    if proxy == "direct":
        for key in PROXY_ENV_KEYS[:6]:
            env.pop(key, None)
    elif proxy:
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
    return env


def proxy_args(proxy: str) -> list[str]:
    return ["-http_proxy", proxy] if proxy and proxy != "direct" else []


def public_proxy(proxy: str) -> str:
    if not proxy:
        return ""
    if proxy == "direct":
        return "direct"
    parsed = urlparse(proxy)
    if not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{port}"


class Cancelled(Exception):
    pass


class IncompleteDownload(RuntimeError):
    pass


@dataclass
class JobConfig:
    url: str
    output: Path
    headers: str = ""
    restart: bool = False
    safety: float = 2.0
    stall_timeout: float = 60.0
    max_retries: int = 10
    keep_parts: bool = False
    proxy: str = ""
    strict_errors: bool = True
    hls_workers: int = 4
    native_hls: bool = True
    trusted_header_origins: tuple[str, ...] = ()

    def __post_init__(self):
        parse_headers(self.headers)
        if not isinstance(self.trusted_header_origins, (tuple, list)):
            raise ValueError("trusted_header_origins 必须是来源列表")
        self.trusted_header_origins = tuple(trusted_origin(value) for value in self.trusted_header_origins)
        validate_proxy(self.proxy)


@dataclass
class Progress:
    status: str = "idle"
    message: str = ""
    percent: float | None = None
    out_time: float = 0.0
    total_time: float | None = None
    speed: str = ""
    size: int | None = None
    part: int = 0
    updated: float = 0.0
    bytes_per_second: float = 0.0
    retries: int = 0


class DownloadJob:
    def __init__(self, cfg: JobConfig, ffmpeg: Path, ffprobe: Path):
        self.cfg = cfg
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.id = f"job-{uuid.uuid4().hex}"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.logs: deque[str] = deque(maxlen=400)
        self.progress = Progress(status="idle", updated=time.monotonic())
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._env = child_env(cfg.proxy)
        self._secrets = secret_values(cfg.headers, cfg.url, cfg.proxy)
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._rate_samples: deque[tuple[float, int]] = deque()
        self._initial_media = 0.0
        self._stalled = False
        self._has_gaps = False

    def start(self) -> None:
        self._set(status="queued", message="等待下载名额…")
        self._thread = threading.Thread(target=self._run_guarded, daemon=True, name="video-download")
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        with self._lock:
            if self.progress.status == "done":
                return
            self._cancel.set()
            proc = self._proc
        if proc is not None and proc.poll() is None:
            self._terminate(proc)

    def snapshot(self, include_logs: bool = True) -> dict:
        with self._lock:
            data = asdict(self.progress)
            logs = list(self.logs)[-200:]
        elapsed = max(0.0, (self.finished_at or time.time()) - self.started_at) if self.started_at else 0.0
        idle = max(0.0, time.monotonic() - data["updated"])
        if idle > 5 or data["status"] != "downloading":
            data["bytes_per_second"] = 0.0
        media_rate = max(0.0, data["out_time"] - self._initial_media) / elapsed if elapsed else 0.0
        data.update(
            {
                "id": self.id,
                "url": self.cfg.url,
                "output": str(self.cfg.output),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "running": self.is_running(),
                "proxy": public_proxy(self.cfg.proxy),
                "strict_errors": self.cfg.strict_errors,
                "elapsed": elapsed,
                "idle_seconds": idle if data["status"] == "downloading" else 0.0,
                "eta_seconds": max(0.0, data["total_time"] - data["out_time"]) / media_rate
                if data["status"] == "downloading" and idle <= 5 and data["total_time"] and media_rate > 0 else None,
                "queue_position": SCHEDULER.position(self.id),
            }
        )
        if include_logs:
            data["logs"] = logs
        return data

    def _set(self, **kw) -> None:
        with self._lock:
            advanced = any(kw[key] > (getattr(self.progress, key) or 0) for key in ("out_time", "size") if kw.get(key) is not None)
            for k, v in kw.items():
                if k == "message":
                    v = redact_text(v, self._secrets)
                setattr(self.progress, k, v)
            if advanced:
                now = time.monotonic()
                self.progress.updated = now
                size = self.progress.size or 0
                self._rate_samples.append((now, size))
                while len(self._rate_samples) > 2 and now - self._rate_samples[0][0] > 5:
                    self._rate_samples.popleft()
                first_time, first_size = self._rate_samples[0]
                if now > first_time:
                    self.progress.bytes_per_second = max(0.0, (size - first_size) / (now - first_time))

    def _log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {redact_text(msg, self._secrets)}"
        with self._lock:
            self.logs.append(line)
        print(line, flush=True)

    def _sleep(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise Cancelled()

    def _run_guarded(self) -> None:
        acquired = False
        try:
            acquired = SCHEDULER.acquire(self.id, self._cancel)
            if not acquired:
                raise Cancelled()
            self._run()
        except Cancelled:
            self._set(status="cancelled", message="已取消，已下载的分段保留")
        except IncompleteDownload as e:
            self._log(str(e))
            self._set(status="incomplete", message=str(e))
        except Exception as e:
            self._log(f"失败：{e}")
            self._set(status="failed", message=str(e))
        finally:
            self.finished_at = time.time()
            if acquired:
                SCHEDULER.release(self.id)
            callback = getattr(self, "on_finish", None)
            if callback:
                try:
                    callback()
                except OSError:
                    self._log("任务已结束，但未能保存缓存索引；缓存文件仍保留。")

    def _run(self) -> None:
        self.started_at = time.time()
        self._set(status="probing", message="正在探测视频信息…")
        parts_dir = self.cfg.output.parent / (self.cfg.output.name + ".parts")
        if self.cfg.native_hls and "m3u8" in self.cfg.url.lower() and not (
            parts_dir.exists() and any(parts_dir.glob("part_*.ts")) and not self.cfg.restart
        ):
            from .hls import UnsupportedPlaylist, cached_playlist, download
            cached = cached_playlist(self.cfg.url, parts_dir) if not self.cfg.restart else None
            if cached:
                playlist, total = cached
                self._log("完整分片缓存可用，直接重新合并，无需访问源站")
                self._finish([playlist], total, parts_dir)
                return
            try:
                playlist, total = download(self, parts_dir)
            except UnsupportedPlaylist as error:
                self._log(f"使用兼容下载方式：{error}")
            else:
                self._finish([playlist], total, parts_dir)
                return
        # ffmpeg forwards -headers to redirects and nested playlists without
        # consulting our origin policy. Refuse scoped headers before any probe.
        legacy_headers(self.cfg.headers)
        info = self._probe(self.cfg.url, timeout=45)
        src_start, total, fmt = info if info else (0.0, None, "")
        if total is not None and total <= 0:
            total = None
        is_hls = "hls" in (fmt or "").lower() or "m3u8" in self.cfg.url.lower()
        self._log(
            f"源信息：start={src_start:.3f}s duration={total if total is not None else '未知'} "
            f"format={fmt or '未知'}"
        )
        self._set(total_time=total)

        out = self.cfg.output
        out.parent.mkdir(parents=True, exist_ok=True)
        parts_dir = out.parent / (out.name + ".parts")
        if self.cfg.restart:
            shutil.rmtree(parts_dir, ignore_errors=True)
        parts_dir.mkdir(parents=True, exist_ok=True)

        valid_parts: list[tuple[Path, float, float]] = []
        parts = sorted(parts_dir.glob("part_*.ts"))
        for i, p in enumerate(parts):
            pinfo = self._probe(p, timeout=20)
            if not pinfo or pinfo[1] is None or pinfo[1] <= 0.3:
                raise IncompleteDownload(f"缓存分段 {p.name} 无法校验，原分段已保留；请检查文件或选择重新开始。")
            valid_parts.append((p, pinfo[0], pinfo[1]))

        resume = src_start
        prev: Path | None = None
        if valid_parts:
            prev = valid_parts[-1][0]
            resume = valid_parts[-1][1] + valid_parts[-1][2]
            self._log(f"发现 {len(valid_parts)} 个已完成分段，从 {resume:.3f}s 继续")
        self._initial_media = max(0.0, resume - src_start)
        self._set(out_time=self._initial_media)
        indices = [int(p.stem.split("_")[1]) for p, _, _ in valid_parts if "_" in p.stem]
        next_index = max(indices) + 1 if indices else 0

        attempts = 0
        while True:
            if self._cancel.is_set():
                raise Cancelled()
            if total is not None and resume >= src_start + total - 0.5:
                self._log("分段已覆盖完整时长，进入合并")
                break

            part = parts_dir / f"part_{next_index:03d}.ts"
            seek_abs = max(src_start, resume - self.cfg.safety)
            rel = max(0.0, seek_abs - src_start)
            cmd = self._build_cmd(part, rel, is_hls)
            self._set(
                status="downloading",
                message=f"下载第 {next_index + 1} 段",
                part=next_index + 1,
            )
            self._log(
                f"开始第 {next_index + 1} 段：seek={rel:.3f}s（目标 {seek_abs:.3f}s）"
            )
            rc = self._run_ffmpeg(cmd, src_start, total)
            if self._cancel.is_set():
                raise Cancelled()

            pinfo = self._probe(part, timeout=20)
            if pinfo and pinfo[1] is not None and pinfo[1] > 0.3:
                p_start, p_dur, _ = pinfo
                old_resume = resume
                new_resume = p_start + p_dur
                if new_resume > old_resume + 0.2:
                    if prev is not None and p_start < resume:
                        self._trim(prev, p_start)
                    elif prev is not None and p_start > resume + 1.0:
                        self._has_gaps = True
                        self._log(f"警告：接缝处可能有约 {p_start - resume:.1f}s 缺口")
                    prev = part
                    resume = new_resume
                    next_index += 1
                    attempts = 0
                    self._log(
                        f"第 {next_index} 段完成：{p_start:.3f}s ~ {resume:.3f}s（{p_dur:.3f}s）"
                    )
                    if rc == 0 and (total is None or resume >= src_start + total - self._tolerance(total)):
                        break
                    if rc == 0:
                        self._log("下载提前结束，保留已下载分段并继续续传")
                else:
                    part.unlink(missing_ok=True)
                    attempts += 1
                    if attempts >= self.cfg.max_retries:
                        raise RuntimeError(f"连续失败 {attempts} 次，已停止")
                    self._log(f"本次进度没有前进（{attempts}/{self.cfg.max_retries}），稍后重试")
                    self._sleep(min(30, 2 * attempts))
            else:
                part.unlink(missing_ok=True)
                attempts += 1
                if attempts >= self.cfg.max_retries:
                    raise RuntimeError(f"连续失败 {attempts} 次，已停止")
                self._log(f"本次失败（{attempts}/{self.cfg.max_retries}），稍后重试")
                self._sleep(min(30, 2 * attempts))

        final_parts = sorted(parts_dir.glob("part_*.ts"))
        if not final_parts:
            raise RuntimeError("没有下载到任何分段")
        self._finish(final_parts, total, parts_dir)

    @staticmethod
    def _tolerance(total: float) -> float:
        return max(2.0, min(5.0, total * 0.002))

    def _finish(self, parts: list[Path], total: float | None, parts_dir: Path) -> None:
        out = self.cfg.output
        temporary = out.with_name(f"{out.stem}.{self.id}.tmp{out.suffix}")
        self._set(status="merging", message="正在合并并校验完整性…", percent=99.0)
        try:
            self._merge(parts, temporary)
            finfo = self._probe(temporary, timeout=30)
            if self._cancel.is_set():
                raise Cancelled()
            if not finfo or finfo[1] is None or finfo[1] <= 0:
                raise IncompleteDownload("无法验证输出视频，已保留分段，请重试。")
            if self._has_gaps or (total is not None and abs(finfo[1] - total) > self._tolerance(total)):
                raise IncompleteDownload(f"下载不完整：输出 {finfo[1]:.1f} 秒，预期 {total or 0:.1f} 秒；分段已保留。")
            with self._lock:
                if self._cancel.is_set():
                    raise Cancelled()
                os.replace(temporary, out)
                note = "（源时长未知，请检查完整性）" if total is None else ""
                self._set(status="done", message=f"完成：{out}{note}", total_time=total or finfo[1], out_time=finfo[1], percent=100.0)
            if not self.cfg.keep_parts:
                shutil.rmtree(parts_dir, ignore_errors=True)
            self._log(f"时长校验通过：{finfo[1]:.1f} 秒" if total is not None else "输出可读取，但源时长未知，无法核对完整覆盖。")
        finally:
            temporary.unlink(missing_ok=True)

    def _build_cmd(self, part: Path, rel: float, is_hls: bool) -> list[str]:
        cmd = [
            str(self.ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "warning",
        ]
        if self.cfg.strict_errors:
            cmd += ["-xerror"]
        scheme = urlparse(self.cfg.url).scheme.lower()
        if scheme in ("http", "https"):
            cmd += [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_max_retries",
                "10",
                "-reconnect_delay_max",
                "10",
            ]
            cmd += self._network_args()
        if is_hls:
            cmd += ["-seg_max_retry", "10", "-max_reload", "100"]
        cmd += [
            "-stats_period",
            "1",
            "-progress",
            "pipe:1",
            "-nostats",
            "-copyts",
            "-ss",
            f"{rel:.3f}",
            "-i",
            self.cfg.url,
            "-c",
            "copy",
            "-f",
            "mpegts",
            str(part),
        ]
        return cmd

    def _run_ffmpeg(self, cmd: list[str], src_start: float, total: float | None) -> int:
        self._log("执行：" + self._redacted(cmd))
        with self._lock:
            self.progress.updated = time.monotonic()
        byte_base = self.progress.size or 0
        self._stalled = False
        stopped = threading.Event()
        proc = self._spawn(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def drain_stderr() -> None:
            try:
                assert proc.stderr is not None
                for line in proc.stderr:
                    line = line.rstrip()
                    if line:
                        self._log(line)
            finally:
                try:
                    if proc.stderr is not None:
                        proc.stderr.close()
                except Exception:
                    pass

        def watch() -> None:
            while not stopped.is_set() and proc.poll() is None:
                if self._cancel.is_set():
                    self._terminate(proc)
                    return
                with self._lock:
                    last = self.progress.updated
                if last and time.monotonic() - last > self.cfg.stall_timeout:
                    self._stalled = True
                    self._log(
                        f"超过 {self.cfg.stall_timeout:.0f} 秒没有进度，终止本次并重试"
                    )
                    self._terminate(proc)
                    return
                stopped.wait(0.2)

        err_thread = threading.Thread(target=drain_stderr, daemon=True)
        watch_thread = threading.Thread(target=watch, daemon=True)
        err_thread.start()
        watch_thread.start()

        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key == "out_time_us":
                    try:
                        out_time = int(value) / 1_000_000
                    except ValueError:
                        continue
                    out_time = max(self.progress.out_time, out_time - src_start, 0.0)
                    percent = None
                    if total and total > 0:
                        percent = max(
                            0.0, min(99.0, out_time / total * 100)
                        )
                    self._set(out_time=out_time, percent=percent)
                elif key == "total_size":
                    try:
                        self._set(size=max(self.progress.size or 0, byte_base + int(value)))
                    except ValueError:
                        pass
                elif key == "speed":
                    self._set(speed=value.strip())
        finally:
            rc = proc.wait()
            stopped.set()
            err_thread.join(timeout=2)
            watch_thread.join(timeout=2)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass

        if self._cancel.is_set():
            raise Cancelled()
        if rc or self._stalled:
            self._set(retries=self.progress.retries + 1)
        return rc or (1 if self._stalled else 0)

    def _network_args(self) -> list[str]:
        args = ["-rw_timeout", "15000000"]
        headers = legacy_headers(self.cfg.headers)
        if headers:
            args += ["-headers", headers + "\r\n"]
        return args + proxy_args(self.cfg.proxy)

    def _spawn(self, cmd: list[str], **kwargs) -> subprocess.Popen:
        with self._lock:
            if self._cancel.is_set():
                raise Cancelled()
            proc = subprocess.Popen(cmd, env=self._env, creationflags=CREATE_NO_WINDOW, **kwargs)
            self._proc = proc
            return proc

    def _capture(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        proc = self._spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._cancel.is_set():
                    raise Cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=min(0.2, remaining))
                    if self._cancel.is_set():
                        raise Cancelled()
                    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if proc.poll() is None:
                self._terminate(proc)
            proc.communicate()
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    @staticmethod
    def _redacted(cmd: list[str]) -> str:
        out: list[str] = []
        hide = False
        for arg in cmd:
            if hide:
                out.append("<已隐藏>")
                hide = False
                continue
            out.append(redact_text(arg))
            if arg in ("-headers", "-http_proxy"):
                hide = True
        return " ".join(out)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def _probe(self, target: str | Path, timeout: float = 45) -> tuple[float, float | None, str] | None:
        cmd = [str(self.ffprobe)]
        if str(target).lower().startswith(("http://", "https://")):
            cmd += self._network_args()
        cmd += [
            "-v",
            "error",
            "-show_entries",
            "format=start_time,duration,format_name",
            "-of",
            "json",
            str(target),
        ]
        try:
            r = self._capture(cmd, timeout)
        except Cancelled:
            raise
        except Exception:
            return None
        if r.returncode != 0:
            return None
        try:
            data = json.loads(r.stdout or "{}").get("format") or {}
        except json.JSONDecodeError:
            return None

        def number(value) -> float | None:
            try:
                result = float(value)
                return result if math.isfinite(result) else None
            except (TypeError, ValueError):
                return None

        return (
            number(data.get("start_time")) or 0.0,
            number(data.get("duration")),
            str(data.get("format_name") or ""),
        )

    def _trim(self, prev: Path, cut: float) -> None:
        info = self._probe(prev, timeout=20)
        if not info or info[1] is None:
            raise IncompleteDownload("无法校验接缝分段，原文件已保留。")
        p_start, p_dur, _ = info
        want = cut - p_start
        if not (0.05 < want < p_dur - 0.001):
            return
        tmp = prev.with_name(prev.stem + ".trim.ts")
        cmd = [
            str(self.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(prev),
            "-t",
            f"{want:.3f}",
            "-c",
            "copy",
            str(tmp),
        ]
        try:
            r = self._capture(cmd, 600)
        except Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        except Exception:
            tmp.unlink(missing_ok=True)
            raise IncompleteDownload("接缝裁剪失败，已保留原始分段。")
        if r.returncode == 0 and tmp.exists():
            os.replace(tmp, prev)
            self._log(f"接缝裁剪：{prev.name} 裁到 {want:.3f}s")
        else:
            tmp.unlink(missing_ok=True)
            raise IncompleteDownload("接缝裁剪失败，已保留原始分段。")

    def _merge(self, parts: list[Path], out: Path) -> None:
        list_path = parts[0].parent / "concat.txt"
        with list_path.open("w", encoding="utf-8", newline="\n") as f:
            for p in parts:
                quoted = p.as_posix().replace("'", "'\\''")
                f.write(f"file '{quoted}'\n")

        cmd = [
            str(self.ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "warning",
        ]
        if self.cfg.strict_errors:
            cmd += ["-xerror"]
        if len(parts) == 1 and parts[0].suffix == ".m3u8":
            cmd += ["-protocol_whitelist", "file,crypto,data", "-allowed_extensions", "ALL", "-i", str(parts[0])]
        else:
            cmd += ["-f", "concat", "-safe", "0", "-i", str(list_path)]
        cmd += ["-c", "copy"]
        if out.suffix.lower() in (".mp4", ".m4v", ".mov"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(out))

        self._log("合并命令：" + " ".join(cmd))
        try:
            r = self._capture(cmd, 3600)
        except Cancelled:
            raise
        except Exception as e:
            raise RuntimeError(f"合并失败：{e}") from e
        if r.returncode != 0:
            detail = (r.stderr or "").strip().splitlines()
            for line in detail[:10]:
                self._log(line)
            meaningful = next((line for line in detail if any(word in line.lower() for word in ("corrupt", "unable", "invalid", "error opening", "denied")) and "terminating thread" not in line.lower()), None)
            raise RuntimeError("合并失败：" + (meaningful or (detail[-1] if detail else f"退出码 {r.returncode}")))
