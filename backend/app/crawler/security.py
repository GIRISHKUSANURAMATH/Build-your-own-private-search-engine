"""
SSRF protection for the crawler.

SSRF (Server-Side Request Forgery): if a server fetches any URL it is given,
an attacker can make it fetch URLs the attacker cannot reach directly:
    http://127.0.0.1:8000/...          services on the same machine
    http://192.168.1.1/                the home router's admin page
    http://169.254.169.254/latest/...  cloud metadata (credentials!)

The crawler is reachable through POST /crawl, so before EVERY request
(including every redirect hop) we:
1. accept only http/https on normal web ports (80, 443, 8080, 8443 unless private networks are allowed);
2. resolve the host name to IP addresses ourselves;
3. refuse if ANY address is private, loopback, link-local, multicast, reserved
   or unspecified (Python's ipaddress.is_global does the classification).

LIMITATION: the HTTP client resolves the name again when connecting, so a
hostile DNS server could answer differently the second time ("DNS
rebinding"). Fully closing that gap needs connecting to the checked IP
directly; for a single-user local tool the check above is a reasonable bar.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]
ALLOWED_PORTS = {80, 443, 8080, 8443}


class BlockedURLError(ValueError):
    """The URL points somewhere the crawler must not go."""


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def check_url(url: str, *, allow_private: bool = False, resolver: Resolver = system_resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURLError(f"scheme {parts.scheme!r} is not allowed")
    host = parts.hostname
    if not host:
        raise BlockedURLError("URL has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not allow_private and port not in ALLOWED_PORTS:
        raise BlockedURLError(f"port {port} is not allowed")
    if allow_private:
        return
    if host.lower() in ("localhost", "localhost.localdomain") or host.lower().endswith(".localhost"):
        raise BlockedURLError("localhost is not allowed")
    try:
        addresses = [host] if _is_ip(host) else resolver(host)
    except OSError as exc:
        raise BlockedURLError(f"cannot resolve {host}: {exc}") from exc
    if not addresses:
        raise BlockedURLError(f"{host} has no addresses")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not ip.is_global:
            raise BlockedURLError(f"{host} resolves to non-public address {addr}")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False
