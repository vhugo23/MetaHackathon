"""Narrow application-layer error(s) for Day 5A.

``ConfigurationParseError`` is the only application error introduced this
phase (Day 5A plan item 3) — a broad ``ConfigIngestionError`` superclass is
deliberately not created without a second, demonstrated error needing it.
The adapter contract (``domain/errors.py``) returns exactly one of
``NormalizedConfiguration`` or ``ParseError``, never both; when it is a
``ParseError``, ``ConfigIngestionService`` has no normalized configuration to
persist or return, so it raises this exception unchanged through the
application boundary rather than inventing an alternate result shape.
"""

from meta_rne.domain.errors import ParseError


class ConfigurationParseError(Exception):
    """Raised by ``ConfigIngestionService`` when the resolved adapter's
    ``parse()`` call returns a ``ParseError`` value. The original
    ``ParseError`` is preserved verbatim on ``.parse_error`` — never
    discarded or reduced to a message string."""

    def __init__(self, parse_error: ParseError) -> None:
        super().__init__(f"configuration parse failed: {parse_error.code}")
        self.parse_error = parse_error


class IncidentNotFoundError(Exception):
    """Raised by ``ResolveIncidentService`` (Day 7A) when the requested
    ``incident_id`` does not exist. Preserves ``incident_id`` as structured
    data — never only baked into the message string — so a future API layer
    can produce its controlled 404 body without re-parsing anything. No
    FastAPI/Pydantic/HTTP-status dependency here; that mapping belongs to
    the API layer alone."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"incident not found: {incident_id!r}")
        self.incident_id = incident_id


class DeviceNotFoundError(Exception):
    """Raised by ``GetDeviceDriftService`` (Day 9, Gate 3) when the
    requested ``device_id`` does not exist. Preserves ``device_id`` as
    structured data — never only baked into the message string — so a
    future API layer can produce its controlled 404 body without
    re-parsing anything. No FastAPI/Pydantic/HTTP-status dependency here;
    that mapping belongs to the API layer alone."""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"device not found: {device_id!r}")
        self.device_id = device_id
