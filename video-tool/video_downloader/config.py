from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_DIR / "config.json"


def _default_output() -> str:
    for name in ("Downloads", "下载", "Videos"):
        path = Path.home() / name
        if path.is_dir():
            return str(path)
    return str(Path.home())


DEFAULTS = {
    "ffmpeg_path": "",
    "output_dir": _default_output(),
    "port": 8766,
    "stall_timeout": 60,
    "max_retries": 10,
    "safety_seconds": 2.0,
    "keep_parts": False,
    "allow_local_input": False,
    "max_concurrent": 2,
    "hls_workers": 4,
    "strict_errors": True,
    "native_hls": True,
    "trusted_header_origins": [],
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({key: value for key, value in data.items() if key in DEFAULTS})
    except Exception:
        pass

    output = Path(str(cfg.get("output_dir") or "")).expanduser()
    if not output.is_absolute() or not output.parent.exists():
        output = Path(_default_output())
    cfg["output_dir"] = str(output)

    try:
        cfg["port"] = int(cfg.get("port") or 8766)
    except (TypeError, ValueError):
        cfg["port"] = 8766
    try:
        cfg["stall_timeout"] = max(10.0, float(cfg.get("stall_timeout") or 60))
    except (TypeError, ValueError):
        cfg["stall_timeout"] = 60.0
    try:
        cfg["max_retries"] = max(1, int(cfg.get("max_retries") or 10))
    except (TypeError, ValueError):
        cfg["max_retries"] = 10
    try:
        cfg["safety_seconds"] = max(0.0, float(cfg.get("safety_seconds") or 2.0))
    except (TypeError, ValueError):
        cfg["safety_seconds"] = 2.0
    cfg["keep_parts"] = bool(cfg.get("keep_parts"))
    cfg["allow_local_input"] = bool(cfg.get("allow_local_input"))
    for key, default, maximum in (("max_concurrent", 2, 6), ("hls_workers", 4, 8)):
        try:
            cfg[key] = max(1, min(maximum, int(cfg.get(key, default))))
        except (ValueError, TypeError):
            cfg[key] = default
    cfg["strict_errors"] = bool(cfg.get("strict_errors", True))
    cfg["native_hls"] = bool(cfg.get("native_hls", True))
    trusted = cfg.get("trusted_header_origins")
    cfg["trusted_header_origins"] = list(trusted) if isinstance(trusted, list) else []
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="config.", suffix=".json", dir=str(APP_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, CONFIG_PATH)
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass
