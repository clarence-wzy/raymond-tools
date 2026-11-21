from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path
    version: str


class ToolNotFound(RuntimeError):
    pass


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def _from_hint(hint: str | None):
    if not hint:
        return
    p = Path(hint).expanduser()
    if p.is_file():
        yield p
        return
    if p.is_dir():
        for rel in (("bin", _exe("ffmpeg")), (_exe("ffmpeg"),), ("ffmpeg",)):
            q = p.joinpath(*rel)
            if q.is_file():
                yield q


def _candidates():
    home = Path.home()
    if os.name == "nt":
        env = os.environ
        yield Path("D:/tools/ffmpeg/bin/ffmpeg.exe")
        yield Path("C:/ffmpeg/bin/ffmpeg.exe")
        yield Path(env.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg/bin/ffmpeg.exe"
        yield Path(env.get("ProgramData", r"C:\ProgramData")) / "chocolatey/bin/ffmpeg.exe"
        yield home / "scoop/shims/ffmpeg.exe"
        yield home / "ffmpeg/bin/ffmpeg.exe"
        local = env.get("LOCALAPPDATA")
        if local:
            yield Path(local) / "Microsoft/WinGet/Links/ffmpeg.exe"
            for q in Path(local).glob("Microsoft/WinGet/Packages/*/ffmpeg*/bin/ffmpeg.exe"):
                yield q
    elif sys.platform == "darwin":
        yield Path("/opt/homebrew/bin/ffmpeg")
        yield Path("/usr/local/bin/ffmpeg")
        yield Path("/opt/local/bin/ffmpeg")
        yield home / "bin/ffmpeg"
    else:
        yield Path("/usr/bin/ffmpeg")
        yield Path("/usr/local/bin/ffmpeg")
        yield Path("/snap/bin/ffmpeg")


def _version(exe: Path) -> str | None:
    try:
        r = subprocess.run(
            [str(exe), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"ffmpeg version (\S+)", r.stdout or "")
    return m.group(1) if m else "unknown"


def find(ffmpeg_hint: str | None = None) -> Tools:
    ordered: list[Path] = []
    for hint in (ffmpeg_hint, os.environ.get("FFMPEG_PATH")):
        ordered.extend(_from_hint(hint))

    which = shutil.which("ffmpeg")
    if which:
        ordered.append(Path(which))
    ordered.extend(_candidates())

    seen: set[str] = set()
    missing_probe: Path | None = None
    for exe in ordered:
        try:
            key = str(exe.resolve())
        except OSError:
            key = str(exe)
        if key in seen:
            continue
        seen.add(key)
        if not exe.is_file():
            continue
        version = _version(exe)
        if not version:
            continue

        probe = exe.with_name(_exe("ffprobe"))
        if not probe.is_file():
            w = shutil.which("ffprobe")
            if w:
                probe = Path(w)
        if not probe.is_file():
            missing_probe = exe
            continue
        return Tools(exe, probe, version)

    if missing_probe:
        raise ToolNotFound(
            f"找到 ffmpeg（{missing_probe}），但同目录没有 ffprobe；请安装完整版本，或在界面中指定 ffprobe 所在目录"
        )
    raise ToolNotFound(
        "未找到 ffmpeg。请先安装 ffmpeg，或在界面中填写 ffmpeg 可执行文件/所在目录"
    )
