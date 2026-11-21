"""Detect the operating system proxy settings for display only."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

CACHE_SECONDS = 10.0
_cache: dict = {"at": 0.0, "value": None}


def parse_server(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "=" in value:
        items: dict[str, str] = {}
        for part in value.split(";"):
            if "=" in part:
                key, _, item = part.partition("=")
                items[key.strip().lower()] = item.strip()
        value = items.get("http") or items.get("https") or ""
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    return value


def _windows() -> dict:
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        def get(name):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None

        enabled = get("ProxyEnable")
        server = get("ProxyServer")
        pac = get("AutoConfigURL")
    return {
        "http": parse_server(str(server)) if enabled and server else "",
        "pac": str(pac or ""),
    }


def _macos() -> dict:
    try:
        output = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return {"http": "", "pac": ""}
    values: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"\s*(\w+)\s*:\s*(.+)", line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    http = ""
    if values.get("HTTPEnable") == "1" and values.get("HTTPProxy"):
        http = f"http://{values['HTTPProxy']}:{values.get('HTTPPort', '80')}"
    pac = values.get("ProxyAutoConfigURLString", "") if values.get("ProxyAutoConfigEnable") == "1" else ""
    return {"http": http, "pac": pac}


def _system() -> dict:
    if os.name == "nt":
        return _windows()
    if sys.platform == "darwin":
        return _macos()
    return {"http": "", "pac": ""}


def detect(force: bool = False) -> dict:
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    try:
        system = _system()
    except Exception:
        system = {"http": "", "pac": ""}
    value = {
        "system": system,
        "env_http": os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or "",
        "env_no_proxy": os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or "",
    }
    _cache.update(at=now, value=value)
    return value
