"""Bounded parallel downloads and durable checkpoints for TS HLS VOD."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from http.client import HTTPException
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .core import Cancelled
from .network import HeaderPolicy, ScopedRedirectHandler


class UnsupportedPlaylist(Exception):
    pass


def failure_reason(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    reason = getattr(error, "reason", error)
    if isinstance(reason, TimeoutError):
        return "网络请求超时"
    if isinstance(reason, OSError):
        return f"{type(reason).__name__}: {str(reason)[:160]}"
    return type(reason).__name__


class ExplicitProxyHandler(ProxyHandler):
    """Match ffmpeg's explicit-proxy policy even when NO_PROXY is set."""

    def proxy_open(self, request, proxy, protocol):
        parsed = urlparse(proxy)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        if parsed.username is not None:
            credentials = f"{unquote(parsed.username)}:{unquote(parsed.password or '')}"
            request.add_unredirected_header("Proxy-authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
        request.set_proxy(host, "http")
        return None


@dataclass
class Segment:
    url: str
    duration: float
    key: str = ""
    iv: str = ""
    discontinuity: bool = False


def attributes(line: str) -> dict[str, str]:
    return {name: quoted if quoted is not None else plain for name, quoted, plain in (
        (match[0], match[1] or None, match[2])
        for match in re.findall(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))', line.partition(":")[2])
    )}


def parse(text: str, base: str) -> list[Segment]:
    if not text.lstrip("\ufeff \r\n").startswith("#EXTM3U") or "#EXT-X-ENDLIST" not in text:
        raise UnsupportedPlaylist("非静态 HLS 点播清单")
    if any(tag in text for tag in ("#EXT-X-MAP", "#EXT-X-BYTERANGE", "#EXT-X-GAP", "#EXT-X-DEFINE", "#EXT-X-PART:", "#EXT-X-SESSION-KEY")):
        raise UnsupportedPlaylist("此清单需要 ffmpeg 的扩展 HLS 支持")
    duration = None
    sequence = 0
    key = iv = ""
    discontinuity = False
    segments = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.partition(":")[2])
        elif line.startswith("#EXT-X-KEY:"):
            attrs = attributes(line)
            method = attrs.get("METHOD")
            if method == "NONE":
                key = iv = ""
            elif method == "AES-128" and attrs.get("KEYFORMAT", "identity") == "identity" and attrs.get("URI"):
                key, iv = urljoin(base, attrs["URI"]), attrs.get("IV", "")
                if iv and not re.fullmatch(r"0[xX][0-9a-fA-F]{1,32}", iv):
                    raise UnsupportedPlaylist("加密 IV 格式不受支持")
            else:
                raise UnsupportedPlaylist("该加密方式需要 ffmpeg 兼容下载")
        elif line.startswith("#EXTINF:"):
            duration = float(line.partition(":")[2].split(",")[0])
            if not math.isfinite(duration) or duration <= 0:
                raise UnsupportedPlaylist("分片时长无效")
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        elif line and not line.startswith("#"):
            if duration is None:
                raise UnsupportedPlaylist("分片缺少时长信息")
            if len(segments) >= 100000 or sequence < 0:
                raise UnsupportedPlaylist("清单分片数量或序号超出支持范围")
            segments.append(Segment(urljoin(base, line), duration, key, iv or f"0x{sequence:032x}", discontinuity))
            sequence += 1
            duration, discontinuity = None, False
    if not segments:
        raise UnsupportedPlaylist("清单中没有分片")
    return segments


def cached_playlist(url: str, directory: Path) -> tuple[Path, float] | None:
    playlist = directory / "local.m3u8"
    try:
        state = json.loads((directory / "hls-state.json").read_text())
        if state.get("source_hash") != hashlib.sha256(url.encode()).hexdigest():
            return None
        lines = playlist.read_text().splitlines()
        files = [line for line in lines if line and not line.startswith("#")]
        durations = [float(line.partition(":")[2].split(",")[0]) for line in lines if line.startswith("#EXTINF:")]
        completed = state["completed"]
        if not files or len(files) != len(durations) or len(completed) != len(files) or any(not math.isfinite(duration) or duration <= 0 for duration in durations):
            return None
        for index, name in enumerate(files):
            if name != f"segment_{index:06d}.ts" or (directory / name).stat().st_size != completed[str(index)]:
                return None
        for key in re.findall(r'URI="([^"]+)"', "\n".join(lines)):
            if not re.fullmatch(r"key_\d+\.key", key) or (directory / key).stat().st_size != 16:
                return None
        return playlist, sum(durations)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def download(job, directory: Path) -> tuple[Path, float]:
    stop = threading.Event()
    policy = HeaderPolicy(job.cfg.url, job.cfg.headers, job.cfg.trusted_header_origins)
    # ffmpeg's proxy policy is shared; direct mode bypasses environment proxies.
    proxy = job.cfg.proxy
    environment_proxy = job._env.get("http_proxy") or job._env.get("HTTP_PROXY")
    inherited = {"http": environment_proxy, "https": environment_proxy} if environment_proxy else {}
    proxies = {} if proxy == "direct" else {"http": proxy, "https": proxy} if proxy else inherited

    def check():
        if job._cancel.is_set() or stop.is_set():
            raise Cancelled()

    def receive(url: str, destination: Path | None = None, limit: int = 4 * 1024 * 1024) -> tuple[bytes, str]:
        if urlparse(url).scheme not in ("http", "https"):
            raise UnsupportedPlaylist("清单包含非 HTTP 资源")
        check()
        handler = ExplicitProxyHandler(proxies) if proxy and proxy != "direct" else ProxyHandler(proxies)
        opener = build_opener(handler, ScopedRedirectHandler(policy))
        try:
            response = opener.open(Request(url, headers=policy.for_url(url)), timeout=min(15, job.cfg.stall_timeout))
        except HTTPError as error:
            error.close()
            raise
        with response:
            final_url = response.geturl()
            expected = response.headers.get("Content-Length")
            chunks, size = [], 0
            stream = destination.open("wb") if destination else None
            try:
                while True:
                    check()
                    block = response.read1(64 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if stream:
                        stream.write(block)
                        with job._lock:
                            job._set(size=(job.progress.size or 0) + len(block))
                    else:
                        if size > limit:
                            raise UnsupportedPlaylist("清单或密钥过大")
                        chunks.append(block)
                if not size or (expected is not None and size != int(expected)):
                    raise OSError("分片响应不完整")
                return b"".join(chunks), final_url
            finally:
                if stream:
                    stream.close()

    url = job.cfg.url
    for _ in range(5):
        try:
            content, base = receive(url)
            text = content.decode("utf-8-sig")
        except (OSError, HTTPException, UnicodeError) as error:
            raise UnsupportedPlaylist(f"清单请求需要兼容处理（{type(error).__name__}）") from error
        if "#EXT-X-STREAM-INF:" not in text:
            break
        if "#EXT-X-MEDIA:" in text:
            raise UnsupportedPlaylist("独立音轨清单使用兼容下载")
        variants, bandwidth = [], None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                try:
                    bandwidth = int(attributes(line).get("BANDWIDTH", "0"))
                except ValueError as error:
                    raise UnsupportedPlaylist("多码率清单字段无效") from error
            elif line and not line.startswith("#") and bandwidth is not None:
                variants.append((bandwidth, urljoin(base, line)))
                bandwidth = None
        if not variants:
            raise UnsupportedPlaylist("无法解析多码率清单")
        url = max(variants)[1]
    else:
        raise UnsupportedPlaylist("清单嵌套过深")
    try:
        segments = parse(text, base)
    except (ValueError, OverflowError) as error:
        raise UnsupportedPlaylist("清单字段格式无效") from error
    # Validate before writing anything, so unsupported formats can safely fall back.
    if any(urlparse(value).scheme not in ("http", "https") for s in segments for value in (s.url, s.key) if value):
        raise UnsupportedPlaylist("清单包含非 HTTP 资源")
    fingerprint = hashlib.sha256(json.dumps([vars(s) for s in segments], sort_keys=True).encode()).hexdigest()
    total = sum(segment.duration for segment in segments)
    checkpoint = directory / "hls-state.json"
    state = {"fingerprint": fingerprint, "completed": {}}
    if checkpoint.exists() and not job.cfg.restart:
        try:
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise RuntimeError("分片记录不可读，已保留缓存，请更换输出名或选择重新开始。") from error
        if not isinstance(state, dict) or not isinstance(state.get("completed"), dict):
            raise RuntimeError("分片记录无效，已保留缓存。")
        if state.get("fingerprint") != fingerprint:
            raise RuntimeError("源清单已变化，已保留旧缓存，请更换输出名或选择重新开始。")
    if job.cfg.restart and directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    state["source_hash"] = hashlib.sha256(job.cfg.url.encode()).hexdigest()

    def save_state():
        temporary = checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, checkpoint)

    completed = state["completed"]
    for index in list(completed):
        if not index.isdigit() or not 0 <= int(index) < len(segments) or type(completed[index]) is not int or completed[index] <= 0:
            raise RuntimeError("分片记录无效，已保留缓存。")
        path = directory / f"segment_{int(index):06d}.ts"
        if not path.is_file() or path.stat().st_size != completed[index]:
            del completed[index]
    cached_duration = sum(segments[int(index)].duration for index in completed)
    job._initial_media = cached_duration
    job._set(status="downloading", total_time=total, out_time=cached_duration, percent=min(99, cached_duration / total * 100), message=f"HLS 分片下载：{len(completed)}/{len(segments)}")
    save_state()
    job._log(f"HLS 共 {len(segments)} 个分片，复用 {len(completed)} 个缓存，并发 {job.cfg.hls_workers}")

    def fetch(index: int):
        path = directory / f"segment_{index:06d}.ts"
        temporary = path.with_suffix(".downloading")
        try:
            for attempt in range(job.cfg.max_retries):
                check()
                try:
                    receive(segments[index].url, temporary)
                    check()
                    os.replace(temporary, path)
                    return index, path.stat().st_size
                except (OSError, ValueError, HTTPException) as error:
                    with job._lock:
                        job._set(retries=job.progress.retries + 1)
                    if attempt + 1 >= job.cfg.max_retries:
                        raise RuntimeError(f"第 {index + 1} 个分片下载失败，缓存已保留（{failure_reason(error)}）。") from error
                    job._log(f"第 {index + 1} 个分片重试 {attempt + 1}/{job.cfg.max_retries}：{failure_reason(error)}")
                    if job._cancel.wait(min(8, 2 ** attempt)) or stop.is_set():
                        check()
                    check()
        finally:
            temporary.unlink(missing_ok=True)

    keys = {}
    for segment in segments:
        if segment.key and segment.key not in keys:
            check()
            key, _ = receive(segment.key, limit=16)
            if len(key) != 16:
                raise RuntimeError("AES-128 密钥长度错误，已保留分片。")
            name = f"key_{len(keys):04d}.key"
            key_path = directory / name
            with key_path.open("wb") as stream:
                os.chmod(key_path, 0o600)
                stream.write(key)
            keys[segment.key] = name

    pool = ThreadPoolExecutor(max_workers=job.cfg.hls_workers, thread_name_prefix="hls")
    futures = []
    try:
        futures = [pool.submit(fetch, index) for index in range(len(segments)) if str(index) not in completed]
        covered = cached_duration
        pending = 0
        last_saved = time.monotonic()
        for future in as_completed(futures):
            check()
            index, size = future.result()
            completed[str(index)] = size
            covered += segments[index].duration
            pending += 1
            now = time.monotonic()
            if pending >= 32 or now - last_saved >= 1:
                save_state()
                pending = 0
                last_saved = now
            job._set(out_time=covered, percent=min(99, covered / total * 100), part=len(completed), message=f"HLS 分片下载：{len(completed)}/{len(segments)}")
    finally:
        stop.set()
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        # A failing future must not discard checkpoints from its successful peers.
        for future in futures:
            if future.done() and not future.cancelled() and future.exception() is None:
                index, size = future.result()
                completed[str(index)] = size
        save_state()

    check_cancelled = job._cancel.is_set()
    if check_cancelled:
        raise Cancelled()
    playlist = directory / "local.m3u8"
    with playlist.open("w", encoding="utf-8") as stream:
        stream.write(f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{math.ceil(max(s.duration for s in segments))}\n#EXT-X-MEDIA-SEQUENCE:0\n")
        for index, segment in enumerate(segments):
            if segment.discontinuity:
                stream.write("#EXT-X-DISCONTINUITY\n")
            if segment.key:
                stream.write(f'#EXT-X-KEY:METHOD=AES-128,URI="{keys[segment.key]}",IV={segment.iv}\n')
            else:
                stream.write("#EXT-X-KEY:METHOD=NONE\n")
            stream.write(f"#EXTINF:{segment.duration},\nsegment_{index:06d}.ts\n")
        stream.write("#EXT-X-ENDLIST\n")
    return playlist, total
