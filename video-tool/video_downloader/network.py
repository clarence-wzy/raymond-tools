"""Scope user-supplied headers to explicitly trusted HTTP origins."""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler

PUBLIC_HEADERS = {"accept", "accept-language", "user-agent"}
TRANSPORT_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}


def origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("请求头来源必须为有效的 HTTP/HTTPS 地址")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("HTTP 资源端口必须为 1 到 65535")
    return f"{parsed.scheme}://{host}:{port}"


def trusted_origin(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("可信请求头来源必须是字符串")
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("可信请求头来源只填写协议、主机和端口，例如 https://cdn.example.com")
    return origin(value)


def parse_headers(text: str) -> dict[str, str]:
    if len(text) > 16384:
        raise ValueError("请求头过长")
    headers = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, separator, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(ord(char) < 32 and char != "\t" for char in value):
            raise ValueError("请求头格式无效，请每行填写 Name: Value")
        if name.lower() in TRANSPORT_HEADERS:
            raise ValueError(f"不允许自定义传输请求头：{name}")
        headers[name.lower()] = value
    return headers


def validate_proxy(proxy: str) -> None:
    if not proxy or proxy == "direct":
        return
    try:
        parsed = urlsplit(proxy)
        port = parsed.port
    except ValueError:
        raise ValueError("代理地址或端口无效，端口必须为 1 到 65535") from None
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("代理地址格式应为 http://host:port，或 direct 表示直连")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("代理端口必须为 1 到 65535")


class HeaderPolicy:
    def __init__(self, source: str, headers: str, trusted: tuple[str, ...] = ()):
        self.source = origin(source)
        self.trusted = {self.source, *(trusted_origin(value) for value in trusted)}
        self.headers = parse_headers(headers)

    def for_url(self, url: str) -> dict[str, str]:
        target = origin(url)
        # A trusted CDN must not turn an HTTPS credential into plaintext HTTP.
        if self.source.startswith("https:") and target.startswith("http:"):
            return {}
        return dict(self.headers) if target in self.trusted else {}


class ScopedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, policy: HeaderPolicy):
        self.policy = policy

    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Validate scheme before urllib can follow a redirect to another protocol.
        outgoing = self.policy.for_url(newurl)
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None:
            redirected.headers.clear()
            redirected.unredirected_hdrs.clear()
            for name, value in outgoing.items():
                redirected.add_header(name, value)
        return redirected


def legacy_headers(text: str) -> str:
    headers = parse_headers(text)
    if headers.keys() - PUBLIC_HEADERS:
        raise ValueError(
            "兼容下载无法安全限制 Cookie、Authorization、Referer 等请求头的发送范围。"
            "请使用原生 HLS 分片下载，或移除这些请求头后重试。"
        )
    return "\r\n".join(f"{name}: {value}" for name, value in headers.items())
