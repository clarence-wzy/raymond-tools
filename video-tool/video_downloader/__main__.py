"""Command line interface for the resumable video downloader."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config
from .core import DownloadJob, JobConfig
from .locator import ToolNotFound, find


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="断点续传视频下载；只调用 ffmpeg，不修改源文件。")
    cli.add_argument("url", help="m3u8 / MP4 / MPEG-TS 地址")
    cli.add_argument("output", type=Path, help="输出文件，例如 D:/videos/out.mp4")
    cli.add_argument("--headers", default="", help="按行分隔的 HTTP 请求头，例如 Cookie / Referer")
    cli.add_argument("--trusted-header-origin", action="append", default=[], help="允许接收请求头的额外来源，例如 https://cdn.example.com；可重复指定")
    cli.add_argument("--restart", action="store_true", help="删除已有分段，从头开始，成功后可覆盖输出文件")
    cli.add_argument("--keep-parts", action="store_true", help="合并成功后保留 .parts 分段")
    cli.add_argument("--safety", type=float, default=2.0, help="续传回退秒数，默认 2")
    cli.add_argument("--stall-timeout", type=float, default=60, help="无进度多少秒后重试，默认 60")
    cli.add_argument("--max-retries", type=int, default=10, help="连续失败次数上限，默认 10")
    cli.add_argument("--proxy", default="", help="http://host:port 走指定代理；direct 强制直连；留空跟随环境")
    cli.add_argument("--ffmpeg", default="", help="ffmpeg 可执行文件或所在目录")
    cli.add_argument("--tolerant", action="store_true", help="允许 ffmpeg 容错处理坏包，可能影响画质；仍执行时长校验")
    cli.add_argument("--hls-workers", type=int, choices=range(1, 9), default=None, help="HLS 分片并发数（1–8）")
    cli.add_argument("--legacy", action="store_true", help="仅使用 ffmpeg 时间续传方式")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    cfg = config.load()
    try:
        tools = find(args.ffmpeg or cfg.get("ffmpeg_path") or None)
    except ToolNotFound as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    try:
        job = DownloadJob(
            JobConfig(
                url=args.url,
                output=args.output,
                headers=args.headers,
                restart=args.restart,
                safety=args.safety,
                stall_timeout=args.stall_timeout,
                max_retries=args.max_retries,
                keep_parts=args.keep_parts,
                proxy=args.proxy,
                strict_errors=not args.tolerant,
                hls_workers=args.hls_workers or cfg["hls_workers"],
                native_hls=not args.legacy and cfg["native_hls"],
                trusted_header_origins=tuple(cfg["trusted_header_origins"]) + tuple(args.trusted_header_origin),
            ),
            tools.ffmpeg,
            tools.ffprobe,
        )
    except ValueError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    job.start()
    last = ""
    try:
        while job.is_running():
            state = job.snapshot()
            percent = state.get("percent")
            line = f"{state['status']} {percent:.1f}%" if percent is not None else state["status"]
            if line != last:
                print(line, file=sys.stderr, flush=True)
                last = line
            time.sleep(1)
    except KeyboardInterrupt:
        job.cancel()
        print("已请求取消；分段会保留，下次可继续。", file=sys.stderr)

    state = job.snapshot()
    print(f"{state['status']}：{state['message']}", file=sys.stderr)
    if state["status"] != "done":
        for line in state["logs"][-10:]:
            print(line, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
