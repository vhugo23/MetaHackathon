"""API-layer clock boundary (Day 5B).

``utc_now`` is the production default injected into ``create_app`` — the
only place `application`/`domain` code's `observed_at` values originate
from a real clock read (architecture.md Section 4.1's Day 5A correction:
``ConfigIngestionService`` itself takes no ``Clock`` dependency). An
injected clock that returns a non-UTC or naive value is a server-
composition failure, not caller input, so it is raised as
``InvalidClockError`` — deliberately not a ``ValueError`` — so it is never
caught by the general caller-``ValueError``-to-422 mapping (api/errors.py)
and instead falls through to the framework's normal unmapped-exception
handling.
"""

from datetime import UTC, datetime


class InvalidClockError(RuntimeError):
    """Raised when an injected clock callable does not return a
    timezone-aware UTC ``datetime``."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise InvalidClockError("injected clock did not return a timezone-aware UTC datetime")
    return value
