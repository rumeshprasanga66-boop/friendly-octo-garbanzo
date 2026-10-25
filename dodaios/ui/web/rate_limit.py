"""Rate limiting for the local API, per client and per route class.

``aiolimiter>=1.1`` has been a CORE dependency of this project from the start,
and until now nothing imported it: the package was installed on every machine,
including the headless VPS, and used by nothing. This module is what it was
declared for, so the limiting is built on ``aiolimiter.AsyncLimiter`` rather
than a second hand-rolled bucket.

The threat is not a remote attacker — the API is loopback-first and guarded by
:mod:`dodaios.ui.web.surface_security`. It is a local one: a runaway script, a
stuck browser tab retry loop, or another process on the same machine can
otherwise hammer an endpoint that spawns work (a brain turn, a mission, a model
download) without limit. A cheap fixed ceiling keeps one misbehaving client
from starving the machine the user is sitting at.

Design notes:

* Pure ASGI, no FastAPI/Starlette import — the same reason as
  ``surface_security``: applying it must not pull those import graphs onto the
  boot path.
* Buckets are keyed by ``(client, route-class)``. The class is chosen from the
  path, so a burst of cheap reads cannot exhaust the budget for a real turn.
* Limiting is ON by default at a generous ceiling: the failure it prevents is
  worse than the delay it adds. It can be switched off or tuned from config —
  see ``docs/rate-limiting.md``.
* A rejected request gets ``429`` with ``Retry-After``, never a hang and never
  a silent drop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from aiolimiter import AsyncLimiter

log = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: Requests per minute allowed for each route class.
ROUTE_CLASSES: dict[str, int] = {
    # Cheap reads: generous. A UI polling status must never be limited.
    "read": 600,
    # State changes: a person clicking, or a script doing setup.
    "write": 120,
    # Anything that starts real work — a brain turn, a mission, a build, a
    # model download. This is the ceiling that actually protects the machine.
    "expensive": 30,
}

DEFAULT_CLASS = "write"

#: Path prefixes that mark an expensive route. These spawn work: model calls,
#: agent missions, downloads, builds, installs.
_EXPENSIVE_PREFIXES: tuple[str, ...] = (
    "/api/agent_chat",
    "/api/missions",
    "/api/agent_server",
    "/api/self_build",
    "/api/local_models",
    "/api/computer_use",
    "/api/marketplace",
    "/api/downloads",
    "/api/update",
    "/api/brain",
    "/api/voice",
    "/api/telephony",
    "/api/email",
    "/api/chat",
)

#: Path prefixes that are always exempt: the UI must load, and a health check
#: must always answer, or a limited client looks like a dead app.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/openapi.json",
    "/api/_swagger",
    "/assets/",
    "/static/",
    "/favicon",
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The smallest ceiling a class may be configured to, so a typo in config
#: cannot accidentally lock the user out of their own assistant.
MIN_PER_MINUTE = 6


def route_class(path: str) -> str:
    """The limiting class for a request path."""
    if path.startswith(_EXEMPT_PREFIXES):
        return "exempt"
    if path.startswith(_EXPENSIVE_PREFIXES):
        return "expensive"
    return DEFAULT_CLASS


def _client_key(scope: Scope) -> str:
    """A stable per-client key: the peer address, or ``"local"`` if absent."""
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "local"


class RateLimitMiddleware:
    """Per-client, per-route-class limiter, as pure ASGI middleware.

    ``enabled=False`` makes the middleware a pass-through, so the layer can
    stay in the stack and limiting can be switched off from config without
    rebuilding the app.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        per_minute: dict[str, int] | None = None,
        burst: float = 2.0,
        max_buckets: int = 4096,
    ) -> None:
        self.app = app
        self.enabled = enabled
        ceilings = dict(ROUTE_CLASSES)
        for name, value in (per_minute or {}).items():
            if name in ceilings and value > 0:
                ceilings[name] = max(MIN_PER_MINUTE, int(value))
        self.ceilings = ceilings
        # ``burst`` above 1 lets a short spike through (opening a screen fires
        # several calls at once) while the steady rate stays at the ceiling.
        # Each entry remembers the loop its limiter was built on: AsyncLimiter
        # caches the running loop, and reusing one across loops is undefined
        # behaviour (it warns, and a restarted loop leaves stale futures). A
        # changed loop replaces the limiter instead of reusing it.
        self._limiters: dict[tuple[str, str], tuple[Any, AsyncLimiter]] = {}
        self._burst = max(1.0, burst)
        self._max_buckets = max(16, int(max_buckets))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        cls = route_class(path)
        if cls == "exempt":
            await self.app(scope, receive, send)
            return

        # A cheap read costs less than a write, so a status poll cannot trade
        # its budget for the budget that guards a real turn.
        method = str(scope.get("method", "GET")).upper()
        cost = 0.25 if method in _SAFE_METHODS else 1.0
        client = _client_key(scope)
        limiter = self._limiter(client, cls)
        if not limiter.has_capacity(cost):
            retry = self._retry_after(cls)
            log.warning(
                "rate limit: %s %s from %s (class=%s) — retry in %.1fs",
                method,
                path,
                client,
                cls,
                retry,
            )
            await self._reject(send, retry)
            return

        await limiter.acquire(cost)
        await self.app(scope, receive, send)

    def _limiter(self, client: str, cls: str) -> AsyncLimiter:
        key = (client, cls)
        loop = asyncio.get_running_loop()
        entry = self._limiters.get(key)
        if entry is not None and entry[0] is loop:
            return entry[1]
        if entry is None and len(self._limiters) >= self._max_buckets:
            # Bound memory. Entries whose loop is gone can never be reused, so
            # they are dropped first; if that was not enough, drop the oldest.
            dead = [k for k, (bound, _) in self._limiters.items() if bound.is_closed()]
            for stale in dead:
                del self._limiters[stale]
            if len(self._limiters) >= self._max_buckets:
                for stale in list(self._limiters)[: self._max_buckets // 2]:
                    del self._limiters[stale]
        per_minute = self.ceilings.get(cls, ROUTE_CLASSES[DEFAULT_CLASS])
        limiter = AsyncLimiter(per_minute * self._burst, 60.0)
        self._limiters[key] = (loop, limiter)
        return limiter

    def _retry_after(self, cls: str) -> float:
        """A conservative wait hint for a client that just hit the ceiling."""
        per_minute = self.ceilings.get(cls, ROUTE_CLASSES[DEFAULT_CLASS])
        return max(1.0, 60.0 / per_minute)

    @staticmethod
    async def _reject(send: Send, retry: float) -> None:
        body = b'{"error":"rate_limited","detail":"Too many requests. Slow down."}'
        seconds = max(1, int(retry + 0.999))
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(seconds).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
