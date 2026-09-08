"""What the outside world calls this server.

Every URL Paperstand puts in a response is root-relative — except in the OPDS
feed, where the specification requires absolute ones. An absolute URL needs the
scheme and the host the *client* used, and behind a reverse proxy neither is the
one the request arrived with: the proxy terminates TLS and talks plain HTTP to
the container, on a host name of its own.

The proxy says what it did in ``X-Forwarded-Proto`` and ``X-Forwarded-Host``, and
this middleware believes those headers — but only from the addresses listed in
``PAPERSTAND_TRUSTED_PROXIES``. They are trivially forged by anyone who can reach
the port, and a forged host is a link in somebody's catalogue pointing at a host
of the attacker's choosing, so the default trusts loopback only.

Uvicorn ships a middleware that does part of this, and Paperstand used to lean on
it. It is not used any more, for two reasons: it does not look at
``X-Forwarded-Host`` at all, and it lives outside the ASGI application, so the
test client never went through it. Doing the whole job here means the code that
runs behind a real proxy is the code the tests exercise.
"""

from __future__ import annotations

import ipaddress
import re

from starlette.types import ASGIApp, Receive, Scope, Send

#: The value that trusts every client, for a port only the proxy can reach.
TRUST_ANY = "*"

#: Schemes a proxy may claim. Anything else is ignored rather than echoed into
#: a URL.
SCHEMES = frozenset({"http", "https", "ws", "wss"})

#: What a forwarded host may look like: a name or an address, with an optional
#: port, and nothing that could end a URL and start something else.
SAFE_HOST = re.compile(r"^[A-Za-z0-9._~-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")


class TrustedProxies:
    """The clients whose ``X-Forwarded-*`` headers are believed.

    A comma-separated list of addresses, CIDR blocks, or ``*``. Anything that is
    neither an address nor a network is kept as a literal and compared as a
    string, which is how a Unix socket peer ("unix", or an empty name) can be
    trusted at all.
    """

    def __init__(self, value: str) -> None:
        self.trust_any = value.strip() == TRUST_ANY
        self.addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        self.networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
        self.literals: set[str] = set()
        if self.trust_any:
            return
        for item in value.split(","):
            entry = item.strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    self.networks.add(ipaddress.ip_network(entry, strict=False))
                else:
                    self.addresses.add(ipaddress.ip_address(entry))
            except ValueError:
                self.literals.add(entry)

    def __contains__(self, host: str | None) -> bool:
        if self.trust_any:
            return True
        if not host:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host in self.literals
        return address in self.addresses or any(address in net for net in self.networks)

    def client_from(self, forwarded_for: str) -> str | None:
        """The client address in an ``X-Forwarded-For`` list.

        Each proxy appends the address it heard from, so the list is read
        backwards and the first entry that is not itself a trusted proxy is the
        client. When they are all trusted the client *was* a proxy, and the
        first entry is the best answer available.
        """
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if not hops:
            return None
        if self.trust_any:
            return hops[0]
        for hop in reversed(hops):
            if hop not in self:
                return hop
        return hops[0]


def _host_only(value: str) -> str:
    """The first entry of a comma-separated forwarded header, trimmed."""
    return value.split(",")[0].strip()


class ForwardedHeadersMiddleware:
    """Rewrite the scheme, the host and the client from the proxy's headers.

    The host is rewritten *in the ``Host`` header* rather than kept somewhere on
    the side, so that everything downstream — ``request.base_url``,
    ``request.url_for``, a redirect Starlette generates — agrees about the name
    of the server without having to know this middleware exists.
    """

    def __init__(self, app: ASGIApp, trusted: str = "127.0.0.1") -> None:
        self.app = app
        self.trusted = TrustedProxies(trusted)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if (client[0] if client else None) in self.trusted:
                self._apply(scope)
        await self.app(scope, receive, send)

    def _apply(self, scope: Scope) -> None:
        headers: list[tuple[bytes, bytes]] = list(scope["headers"])
        found: dict[bytes, str] = {}
        for name, value in headers:
            if name in (b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-for"):
                found.setdefault(name, value.decode("latin-1"))

        proto = _host_only(found.get(b"x-forwarded-proto", ""))
        if proto in SCHEMES:
            scope["scheme"] = proto.replace("http", "ws") if scope["type"] == "websocket" else proto

        host = _host_only(found.get(b"x-forwarded-host", ""))
        if host and SAFE_HOST.match(host):
            scope["headers"] = _with_host(headers, host.encode("latin-1"))

        forwarded_for = found.get(b"x-forwarded-for")
        if forwarded_for:
            client = self.trusted.client_from(forwarded_for)
            if client:
                scope["client"] = (client, 0)


def _with_host(headers: list[tuple[bytes, bytes]], host: bytes) -> list[tuple[bytes, bytes]]:
    """The header list with ``Host`` replaced, keeping it where it was."""
    replaced = [(name, host if name == b"host" else value) for name, value in headers]
    if not any(name == b"host" for name, _ in replaced):
        replaced.append((b"host", host))
    return replaced
