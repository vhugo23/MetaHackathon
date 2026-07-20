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
