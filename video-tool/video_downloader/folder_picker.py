"""Open a local folder dialog in a separate process (HTTP runs in threads)."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

_LOCK = threading.Lock()
_MAC_SCRIPT = '''
on run argv
    try
        activate
        set folderPath to choose folder with prompt "选择视频保存目录" default location (POSIX file (item 1 of argv))
        return POSIX path of folderPath
    on error number -128
        return ""
    end try
end run
'''
_TK_SCRIPT = '''
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
try:
    print(filedialog.askdirectory(title="选择视频保存目录", initialdir=sys.argv[1], parent=root, mustexist=True))
finally:
    root.destroy()
'''


def choose(initial: str) -> str | None:
    if not _LOCK.acquire(blocking=False):
        raise ValueError("文件夹选择窗口已打开，请先完成选择或取消。")
    try:
        directory = Path(initial).expanduser()
        if not directory.is_absolute():
            directory = Path.home()
        while not directory.is_dir() and directory != directory.parent:
            directory = directory.parent
        if sys.platform == "darwin":
            command = ["osascript", "-e", _MAC_SCRIPT, str(directory)]
        else:
            command = [sys.executable, "-c", _TK_SCRIPT, str(directory)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as error:
            raise ValueError("选择文件夹超时，请重试。") from error
        except OSError as error:
            raise ValueError("无法打开文件夹选择窗口，请手动填写保存目录。") from error
        if result.returncode:
            raise ValueError("无法打开文件夹选择窗口，请手动填写保存目录；Windows/Linux 请确认 Python 已安装 Tk。")
        selected = result.stdout.rstrip("\r\n")
        if not selected:
            return None
        path = Path(selected)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("选择的文件夹不存在，请重新选择。")
        return str(path)
    finally:
        _LOCK.release()
