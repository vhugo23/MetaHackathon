"""HTTP layer: routing, request/response mapping, status-code mapping.

Depends on `application` only. See architecture.md Section 2.

Day 5B: ``create_app`` (``app.py``) is a controlled composition factory —
no engine/``Session`` is created and no ``DATABASE_URL`` is required at
import time. ``schemas.py`` holds explicit Pydantic request/response
models (success responses are the resource itself, no envelope);
``errors.py`` maps typed exceptions to HTTP status + a direct
``{"code", "detail"}`` body; ``routes.py`` builds the router from
already-constructed `application` services; ``dependencies.py`` holds pure
composition helpers (lazy production engine construction, Slice 1 seeding);
``clock.py`` is the one place a real system clock is read.
"""
