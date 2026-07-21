"""API-layer clock boundary (Day 5B; ``CallableClock`` added Day 7A Gate
7A-C).

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

``CallableClock`` adapts that same ``create_app(clock=...)`` seam — a plain
``Callable[[], datetime]`` — to the structural
``meta_rne.application.incident_resolution.Clock`` protocol
(``.now() -> datetime``) that ``ResolveIncidentService`` depends on. It is
the *only* clock ``create_app`` ever constructs: the identical injected
callable already used for ``POST /devices/{id}/config``'s ``observed_at`` is
reused for incident resolution, never a second independent time source.
Each ``.now()`` call re-validates the callable's return value via
``require_utc`` — an invalid clock fails the same controlled way here as it
does for configuration ingestion.
"""

from collections.abc import Callable
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


class CallableClock:
    """Adapts a ``Callable[[], datetime]`` to the application layer's
    ``Clock`` protocol (``.now() -> datetime``) — no global mutable clock,
    no dependency-injection framework, just one small composition-time
    wrapper around the app factory's existing ``clock`` parameter."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    def now(self) -> datetime:
        return require_utc(self._now())
