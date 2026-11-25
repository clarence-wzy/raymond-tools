#!/usr/bin/env python3
"""Serve local video tools with one Python process."""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from video_downloader import api as video
from video_downloader import config

ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ROOT / "shared" / "index.html",
    "/shared/style.css": ROOT / "shared" / "style.css",
    "/shared/app.js": ROOT / "shared" / "app.js",
    "/shared/favicon.svg": ROOT / "shared" / "favicon.svg",
    "/video_downloader/": ROOT / "video_downloader" / "web" / "index.html",
    "/video_downloader/app.js": ROOT / "video_downloader" / "web" / "app.js",
    "/video_downloader/style.css": ROOT / "video_downloader" / "web" / "style.css",
}

ENDPOINTS = {
    "/api/video_downloader/state": (video.state, 65536, "64 KB"),
    "/api/video_downloader/start": (video.start, 65536, "64 KB"),
    "/api/video_downloader/start_batch": (video.start_batch, 131072, "128 KB"),
    "/api/video_downloader/cancel": (video.cancel, 4096, "4 KB"),
    "/api/video_downloader/cancel_all": (video.cancel_all, 4096, "4 KB"),
    "/api/video_downloader/remove": (video.remove, 4096, "4 KB"),
    "/api/video_downloader/clear_finished": (video.clear_finished, 4096, "4 KB"),
    "/api/video_downloader/logs": (video.logs, 4096, "4 KB"),
    "/api/video_downloader/detect": (video.detect, 65536, "64 KB"),
    "/api/video_downloader/test_proxy": (video.test_proxy, 4096, "4 KB"),
    "/api/video_downloader/open_folder": (video.open_folder, 4096, "4 KB"),
    "/api/video_downloader/select_folder": (video.select_folder, 4096, "4 KB"),
    "/api/video_downloader/settings": (video.settings, 4096, "4 KB"),
    "/api/video_downloader/retry": (video.retry, 65536, "64 KB"),
    "/api/video_downloader/edit_details": (video.edit_details, 4096, "4 KB"),
}


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        # Windows otherwise permits two servers to bind the same local port.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_body(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def local_request(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in allowed or (
            origin is not None and origin not in {f"http://{host}" for host in allowed}
        ):
            self.send_json(403, {"error": "仅允许本机页面访问。"})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        route = urlsplit(self.path).path
        if route == "/video_downloader":
            self.send_response(308)
            self.send_header("Location", "/video_downloader/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = STATIC_FILES.get(route)
        if path is None or not path.is_file():
            self.send_json(404, {"error": "未找到资源。"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_body(200, path.read_bytes(), content_type + "; charset=utf-8")

    def do_POST(self):
        if not self.local_request():
            return
        endpoint = ENDPOINTS.get(urlsplit(self.path).path)
        if endpoint is None:
            self.send_json(404, {"error": "未找到接口。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_json(415, {"error": "请求格式必须为 JSON。"})
            return

        callback, max_request_size, file_limit = endpoint
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= max_request_size:
                self.send_json(413, {"error": f"请求为空或过大，上限为 {file_limit}。"})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求参数无效。")
            self.send_json(200, callback(payload))
        except (ValueError, RuntimeError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(500, {"error": f"处理失败：{error}"})


def main():
    parser = argparse.ArgumentParser(description="启动视频工具的本地 Web 入口。")
    parser.add_argument("--port", type=int, default=None, help="首选端口，默认读取 config.json 或 8766")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    cfg = config.load()
    requested = args.port or int(cfg.get("port") or 8766)
    if not 1 <= requested <= 65535:
        parser.error("端口必须为 1 到 65535。")

    server = None
    for port in range(requested, min(requested + 20, 65536)):
        try:
            server = LocalServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        parser.error("无法绑定本地端口，请使用 --port 指定其他端口。")

    tools = video.refresh_tools()
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Video Tools 已启动：{url}\n按 Ctrl+C 停止。", flush=True)
    if tools is not None:
        print(f"ffmpeg：{tools.ffmpeg}（{tools.version}）", flush=True)
    else:
        print(f"ffmpeg：未找到 - {video.TOOLS_ERROR}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        video.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
