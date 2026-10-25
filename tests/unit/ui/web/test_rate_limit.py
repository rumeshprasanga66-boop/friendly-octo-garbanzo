"""The API rate limiter: it limits, it exempts, and it never hangs.

These tests drive the middleware through a real ASGI app with real ASGI
messages, not mocks — the point is that a request over the ceiling gets a
``429`` with a ``Retry-After`` header instead of reaching the app or hanging.
"""

from __future__ import annotations

import asyncio

from dodaios.ui.web.rate_limit import (
    DEFAULT_CLASS,
    MIN_PER_MINUTE,
    ROUTE_CLASSES,
    RateLimitMiddleware,
    route_class,
)


class _Recorder:
    """A tiny ASGI app that records what reached it and answers 200."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _scope(path: str = "/api/settings", method: str = "GET", client: str = "127.0.0.1"):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "client": (client, 51234),
        "headers": [],
    }


async def _call(app, scope) -> tuple[int, dict[bytes, bytes]]:
    """Run one request; return ``(status, headers)``."""
    captured: dict[str, object] = {}

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
            captured["headers"] = {
                name.lower(): value for name, value in message.get("headers", [])
            }

    await app(scope, receive, send)
    return captured["status"], captured.get("headers", {})


# -- route classification -------------------------------------------------


def test_expensive_routes_are_recognised() -> None:
    assert route_class("/api/agent_chat/send") == "expensive"
    assert route_class("/api/missions/run") == "expensive"
    assert route_class("/api/local_models/pull") == "expensive"


def test_health_and_assets_are_exempt() -> None:
    assert route_class("/api/health") == "exempt"
    assert route_class("/assets/index-abc123.js") == "exempt"


def test_plain_settings_route_uses_the_default_class() -> None:
    assert route_class("/api/settings") == DEFAULT_CLASS


def test_expensive_ceiling_is_lower_than_the_read_ceiling() -> None:
    """The whole point: work-spawning routes must be the tightest budget."""
    assert ROUTE_CLASSES["expensive"] < ROUTE_CLASSES["write"] < ROUTE_CLASSES["read"]


# -- behaviour ------------------------------------------------------------


async def test_requests_under_the_ceiling_pass_through() -> None:
    inner = _Recorder()
    app = RateLimitMiddleware(inner, per_minute={"write": 60, "expensive": 60})
    for _ in range(5):
        status, _ = await _call(app, _scope(method="POST"))
        assert status == 200
    assert inner.calls == 5


async def test_a_flood_is_rejected_with_429_and_retry_after() -> None:
    inner = _Recorder()
    # burst=1 so the ceiling is exactly the limit; 6/min is MIN_PER_MINUTE.
    app = RateLimitMiddleware(inner, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0)
    statuses = []
    for _ in range(MIN_PER_MINUTE + 4):
        status, headers = await _call(app, _scope("/api/missions/run", "POST"))
        statuses.append(status)
        if status == 429:
            assert b"retry-after" in headers
            assert int(headers[b"retry-after"]) >= 1
            assert headers[b"content-type"] == b"application/json"
    assert statuses.count(429) >= 1, f"the flood was never limited: {statuses}"
    assert statuses[0] == 200, "the first request must always be allowed"
    # The rejected ones never reached the app.
    assert inner.calls == statuses.count(200)


async def test_exempt_paths_are_never_limited() -> None:
    inner = _Recorder()
    app = RateLimitMiddleware(inner, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0)
    for _ in range(200):
        status, _ = await _call(app, _scope("/api/health", "GET"))
        assert status == 200
    assert inner.calls == 200


async def test_limits_are_per_client() -> None:
    """One noisy client must not lock out another."""
    inner = _Recorder()
    app = RateLimitMiddleware(inner, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0)
    for _ in range(MIN_PER_MINUTE + 2):
        await _call(app, _scope("/api/missions/run", "POST", client="10.0.0.1"))
    # A different client still gets its first request through.
    status, _ = await _call(app, _scope("/api/missions/run", "POST", client="10.0.0.2"))
    assert status == 200


async def test_reads_do_not_consume_the_write_budget() -> None:
    """A status poll must not exhaust the budget that guards real work."""
    inner = _Recorder()
    app = RateLimitMiddleware(inner, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0)
    for _ in range(50):
        status, _ = await _call(app, _scope("/api/settings", "GET"))
        assert status == 200
    # The expensive class is untouched by those reads.
    status, _ = await _call(app, _scope("/api/missions/run", "POST"))
    assert status == 200


async def test_disabled_middleware_is_a_pass_through() -> None:
    inner = _Recorder()
    app = RateLimitMiddleware(
        inner, enabled=False, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0
    )
    for _ in range(MIN_PER_MINUTE * 3):
        status, _ = await _call(app, _scope("/api/missions/run", "POST"))
        assert status == 200
    assert inner.calls == MIN_PER_MINUTE * 3


async def test_websocket_scopes_pass_through_untouched() -> None:
    """Only HTTP is limited; a WS scope must not be misread as a request."""
    seen: list[str] = []

    async def ws_app(scope, receive, send) -> None:
        seen.append(scope["type"])

    app = RateLimitMiddleware(ws_app)
    await app({"type": "websocket", "path": "/ws"}, None, None)  # type: ignore[arg-type]
    assert seen == ["websocket"]


def test_a_silly_config_value_cannot_lock_the_user_out() -> None:
    """A typo of 1 must be raised to the floor, not honoured literally."""
    app = RateLimitMiddleware(_Recorder(), per_minute={"write": 1})
    assert app.ceilings["write"] == MIN_PER_MINUTE


async def test_bucket_map_stays_bounded() -> None:
    """Many distinct clients must not grow memory without bound."""
    inner = _Recorder()
    app = RateLimitMiddleware(inner, max_buckets=32)
    for index in range(200):
        await _call(app, _scope(client=f"10.0.{index // 250}.{index % 250}"))
    assert len(app._limiters) <= 32


def test_a_limiter_is_not_reused_across_event_loops() -> None:
    """AsyncLimiter binds to a loop; a new loop must get a new limiter."""
    inner = _Recorder()
    app = RateLimitMiddleware(inner, per_minute={"expensive": MIN_PER_MINUTE}, burst=1.0)
    key = ("127.0.0.1", "expensive")

    first_loop = asyncio.new_event_loop()
    try:
        first_loop.run_until_complete(_call(app, _scope("/api/missions/run", "POST")))
    finally:
        first_loop.close()
    first = app._limiters[key][0]
    assert first is first_loop

    # A fresh loop, as a restarted app or a new test would create.
    second_loop = asyncio.new_event_loop()
    try:
        second_loop.run_until_complete(_call(app, _scope("/api/missions/run", "POST")))
    finally:
        second_loop.close()

    bound, _limiter = app._limiters[key]
    assert bound is second_loop, "the limiter kept a binding to a closed loop"
