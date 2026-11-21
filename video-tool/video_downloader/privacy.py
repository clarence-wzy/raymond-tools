"""Sanitize diagnostic text before it reaches a terminal or task log."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

HIDDEN = "<已隐藏>"
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
HEADER_PATTERN = re.compile(
    r"(?im)\b(?:cookie|set-cookie|authorization|proxy-authorization|x-api-key|x-auth-token)\s*:[^\r\n]*"
)


def redact_url(match: re.Match) -> str:
    try:
        parsed = urlsplit(match.group())
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}/{HIDDEN}"
    except ValueError:
        return HIDDEN


def secret_values(headers: str, *urls: str) -> tuple[str, ...]:
    values = set()
    for line in headers.splitlines():
        _, separator, value = line.partition(":")
        if separator and value.strip():
            values.add(value.strip())
            # ffmpeg may echo a cookie or bearer token without its header name.
            for item in value.split(";"):
                if "=" in item:
                    values.add(item.partition("=")[2].strip())
            if value.strip().lower().startswith("bearer "):
                values.add(value.strip()[7:])
    for url in urls:
        try:
            parsed = urlsplit(url)
            # URLs are always masked in full. Avoid replacing short, common
            # non-secret query values (for example id=1) throughout diagnostics.
            values.update(value for name, value in parse_qsl(parsed.query) if len(value) >= 4 or re.search(r"token|sign|auth|secret|key|credential", name, re.IGNORECASE))
            values.update(unquote(value) for value in (parsed.username, parsed.password) if value)
        except ValueError:
            continue
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    text = URL_PATTERN.sub(redact_url, str(text))
    text = HEADER_PATTERN.sub(HIDDEN, text)
    for value in secrets:
        text = text.replace(value, HIDDEN)
    return text
