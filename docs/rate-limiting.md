# API rate limiting

The local API (`dodaios/ui/web/server.py`) is rate limited per client and per
route class. The implementation is `dodaios/ui/web/rate_limit.py`.

## What it protects against

The API is loopback-first and guarded by `surface_security.py`, so the threat
is not a remote attacker. It is a local one: a runaway script, a stuck browser
tab in a retry loop, or another process on the same machine hammering an
endpoint that spawns work — a brain turn, an agent mission, a model download.
Without a ceiling, one misbehaving client can starve the machine the user is
sitting at, which on an 8 GB machine is the difference between a slow answer
and no answer.

## How it works

A token bucket per `(client address, route class)`, built on
`aiolimiter.AsyncLimiter` — a core dependency of this project that nothing
imported until now. Buckets refill continuously at the class's rate.

The route class comes from the request path:

| Class | Ceiling (requests/min) | Applies to |
|---|---|---|
| `read` | 600 | cheap GETs — status polls, listings |
| `write` | 120 | state changes — settings, saves |
| `expensive` | 30 | anything that starts real work: brain turns, missions, model downloads, builds, installs |

A `GET`/`HEAD`/`OPTIONS` request costs 0.25 tokens; anything else costs 1.0. So
a UI that polls status cannot consume the budget that guards real work.

A request over its ceiling gets `429` with a `Retry-After` header. It never
hangs and is never silently dropped.

## Exempt paths

These are never limited, because limiting them would make a working app look
broken:

- `/api/health` — a health check must always answer
- `/api/openapi.json`, `/api/_swagger` — the API schema
- `/assets/`, `/static/`, `/favicon` — the frontend must load

## Configuration

In `jarvis.toml`:

```toml
[ui]
rate_limit_enabled = true
rate_limit_read_per_minute = 600
rate_limit_write_per_minute = 120
rate_limit_expensive_per_minute = 30
```

Limiting is **on by default**. A ceiling configured below the module's floor
(6/min) is raised to the floor rather than honoured, so a typo cannot lock the
user out of their own assistant. Set `rate_limit_enabled = false` to turn
limiting off entirely without rebuilding the app — the middleware stays in the
stack and becomes a pass-through.

## Middleware order

`RateLimitMiddleware` is added *after* `SurfaceSecurity`, which makes
`SurfaceSecurity` the outer layer. A request that fails the Host/Origin check
is rejected before it can consume a rate-limit token, so a hostile client
cannot use `429` responses to probe the security boundary.

## Limits of this control

- It is **per process**. Two app instances on one machine keep separate
  counters.
- The client key is the peer address. Behind a reverse proxy every caller looks
  like the proxy unless the proxy is trusted to set a forwarding header; this
  is deliberate, because trusting a header by default would let any client
  forge its own identity.
- It is a **blunt ceiling**, not a fair scheduler. It stops a runaway client;
  it does not prioritise one user's work over another's.
