"""Bounded public reads, pinned DNS, verified TLS and checked redirects.

No cookies, credentials, proxy environment, automatic retry, or paid API calls.
The connection uses the *validated* IP while TLS authenticates the original
hostname, preventing a second DNS lookup from rebinding to a private address.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit

import certifi
import urllib3

from .models import LibraryError


@dataclass(frozen=True)
class Download:
    body: bytes
    content_type: str
    url: str


class PublicHTTP:
    def get(self, url: str, *, params: dict | None = None, max_bytes: int = 12_000_000) -> Download:
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        try:
            for _ in range(4):
                parts = urlsplit(url)
                if (
                    parts.scheme != "https" or not parts.hostname or parts.username
                    or parts.password or parts.port not in (None, 443) or len(url) > 8192
                ):
                    raise LibraryError("unsafe_url")
                addresses = {item[4][0] for item in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)}
                if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                    raise LibraryError("unsafe_url")
                pool = urllib3.HTTPSConnectionPool(
                    host=sorted(addresses)[0], port=443,
                    server_hostname=parts.hostname, assert_hostname=parts.hostname,
                    cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
                    timeout=urllib3.Timeout(connect=5, read=15), maxsize=1,
                )
                response = None
                try:
                    target = parts.path or "/"
                    if parts.query:
                        target += "?" + parts.query
                    response = pool.urlopen(
                        "GET", target, headers={"Host": parts.hostname, "User-Agent": "AcademicResearch/1.0", "Accept-Encoding": "identity"},
                        retries=False, redirect=False, preload_content=False,
                    )
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location", "")
                        if not location:
                            raise LibraryError("invalid_redirect")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise LibraryError("rate_limited" if response.status == 429 else "http_failed")
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > max_bytes:
                        raise LibraryError("download_too_large")
                    chunks, size = [], 0
                    for block in response.stream(65_536, decode_content=True):
                        size += len(block)
                        if size > max_bytes:
                            raise LibraryError("download_too_large")
                        chunks.append(block)
                    return Download(b"".join(chunks), response.headers.get("Content-Type", "").lower(), url)
                finally:
                    if response is not None:
                        response.close()
                    pool.close()
            raise LibraryError("too_many_redirects")
        except LibraryError:
            raise
        except Exception:
            raise LibraryError("network_failed") from None

    def json(self, url: str, params: dict | None = None) -> object:
        result = self.get(url, params=params, max_bytes=4_000_000)
        try:
            return json.loads(result.body)
        except (ValueError, UnicodeError):
            raise LibraryError("invalid_provider_response") from None
