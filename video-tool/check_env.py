from __future__ import annotations

import platform
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from video_downloader import config  # noqa: E402
from video_downloader.locator import ToolNotFound, find  # noqa: E402


def writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def main() -> int:
    print("Video Tools - 环境检查")
    print("=" * 64)
    print(f"系统     : {platform.platform()}")
    print(f"Python   : {sys.version.split()[0]}  ({sys.executable})")
    print(f"程序目录 : {APP_DIR}")

    ok = True
    if sys.version_info < (3, 10):
        print("Python   : 不满足要求，需要 3.10 或更高")
        ok = False
    else:
        print("Python   : 满足要求（>= 3.10）")

    cfg = config.load()
    print(f"配置文件 : {config.CONFIG_PATH}")
    print(f"保存目录 : {cfg['output_dir']}")
    if writable(Path(cfg["output_dir"])):
        print("保存目录 : 可写")
    else:
        print("保存目录 : 不可写，请在页面中换一个目录")
        ok = False

    try:
        tools = find(cfg.get("ffmpeg_path") or None)
    except ToolNotFound as error:
        print(f"ffmpeg   : 未找到 - {error}")
        print("           Windows: winget install Gyan.FFmpeg   或   choco install ffmpeg")
        print("           macOS  : brew install ffmpeg")
        print("           也可以手动下载后，在页面底部填写 ffmpeg 路径")
        ok = False
    else:
        print(f"ffmpeg   : {tools.ffmpeg}  ({tools.version})")
        print(f"ffprobe  : {tools.ffprobe}")

    print("=" * 64)
    if ok:
        print("检查通过。运行 start.bat（Windows）或 start.command（macOS），或执行 python app.py。")
        return 0
    print("检查未通过，请按上面的提示处理后再启动。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
