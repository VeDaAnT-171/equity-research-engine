"""HTTP retrieval with SSRF defences, size limits, redirect validation and per-host throttling."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from ..errors import ConfigError, FetchError
from ..schemas.company import validate_public_https_url
from ..versioning import ENGINE_VERSION

DEFAULT_MAX_BYTES = 200 * 1024 * 1024  # companyfacts for large filers is tens of MB


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    content_type: str | None
    final_url: str


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def _check_url(url: str) -> None:
    try:
        validate_public_https_url(url)
    except ValueError as exc:
        raise FetchError(str(exc)) from None


def check_resolves_public(host: str, resolver: Callable = socket.getaddrinfo) -> None:
    """Every address the host resolves to must be public (guards against DNS rebinding to internal IPs)."""
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host!r}: {exc}") from None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise FetchError(f"{host!r} resolved to no addresses")
    for addr in addresses:
        if not ipaddress.ip_address(addr.split("%")[0]).is_global:
            raise FetchError(f"{host!r} resolves to non-public address {addr}; refusing to connect")


def read_limited(stream, max_bytes: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = stream.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(f"response exceeds {max_bytes} bytes")
    return b"".join(chunks)


def gunzip_limited(body: bytes, max_bytes: int) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decompressor.decompress(body, max_bytes + 1)
    except zlib.error as exc:
        raise FetchError(f"invalid gzip response: {exc}") from None
    if len(out) > max_bytes or decompressor.unconsumed_tail:
        raise FetchError(f"decompressed response exceeds {max_bytes} bytes")
    return out


def require_contact_user_agent(user_agent: str | None) -> str:
    ua = (user_agent or "").strip()
    if not ua or "@" not in ua:
        raise ConfigError(
            "SEC_USER_AGENT must be set to a name and contact email (SEC fair-access policy), "
            'e.g. SEC_USER_AGENT="Jane Doe jane@example.com" in .env'
        )
    return ua


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver: Callable):
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        check_resolves_public(urlparse(newurl).hostname, self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpFetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 60.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        min_interval_seconds: float = 0.15,  # SEC allows at most 10 requests/second
        resolver: Callable = socket.getaddrinfo,
    ):
        self.user_agent = f"{user_agent.strip()} research-engine/{ENGINE_VERSION}"
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.min_interval = min_interval_seconds
        self._resolver = resolver
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_ValidatingRedirectHandler(resolver))

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last_request.get(host, 0.0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()

    def fetch(self, url: str) -> FetchResult:
        _check_url(url)
        host = urlparse(url).hostname
        check_resolves_public(host, self._resolver)
        self._throttle(host)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = read_limited(response, self.max_bytes)
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                content_type = response.headers.get("Content-Type")
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            deny = exc.headers.get("x-deny-reason") if exc.headers else None
            if deny:
                hint = f" (blocked by a network proxy: {deny}; allow the host in your network settings)"
            elif exc.code == 403 and urlparse(url).hostname == "data.sec.gov":
                hint = " (SEC returns 403 for missing or generic User-Agent headers)"
            else:
                hint = ""
            raise FetchError(f"HTTP {exc.code} for {url}{hint}") from None
        except urllib.error.URLError as exc:
            raise FetchError(f"cannot retrieve {url}: {exc.reason}") from None
        except TimeoutError:
            raise FetchError(f"timed out retrieving {url}") from None
        if encoding == "gzip":
            body = gunzip_limited(body, self.max_bytes)
        return FetchResult(content=body, content_type=content_type, final_url=final_url)
