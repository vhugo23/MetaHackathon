"""Public-demo read-only mode (conference deployment hardening).

``META_RNE_DEMO_READ_ONLY`` (default ``false``) turns on a narrow, HTTP-only
guard: while it is on, every request whose method is not ``GET``, ``HEAD`` or
``OPTIONS`` is answered with a stable ``403`` and never reaches a route. The
guard is plain ASGI middleware, so it is application-wide (any current or
future mutating route is covered) and it only sees HTTP requests. It does not
touch Alembic, the FastAPI lifespan's idempotent policy seeding, or the
application/repository services used outside HTTP (for example the one-time
operational-state seed script), all of which keep working.

The flag parser is strict: only ``true``/``false`` (case-insensitive) are
accepted. A typo raises at startup instead of silently leaving a public demo
writable.
"""

import json
import os

from starlette.types import ASGIApp, Receive, Scope, Send

_DEMO_READ_ONLY_ENV_VAR = "META_RNE_DEMO_READ_ONLY"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DEMO_READ_ONLY_CODE = "demo_read_only"
DEMO_READ_ONLY_DETAIL = "This public demo is read-only."


def parse_demo_read_only(raw_value: str | None) -> bool:
    """Unset or blank is ``False``; ``true``/``false`` (any case) are explicit;
    anything else is a configuration error."""
    if raw_value is None or raw_value.strip() == "":
        return False
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{_DEMO_READ_ONLY_ENV_VAR} must be 'true' or 'false'")


def demo_read_only_from_environment() -> bool:
    return parse_demo_read_only(os.environ.get(_DEMO_READ_ONLY_ENV_VAR))


class DemoReadOnlyMiddleware:
    """Rejects non-safe HTTP methods with ``403`` before routing."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._body = json.dumps(
            {"code": DEMO_READ_ONLY_CODE, "detail": DEMO_READ_ONLY_DETAIL}
        ).encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(self._body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": self._body})
