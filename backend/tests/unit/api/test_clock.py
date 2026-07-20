"""Unit test for the production clock default (Day 5B)."""

from datetime import UTC

from meta_rne.api.clock import utc_now


def test_utc_now__returns_a_timezone_aware_utc_datetime() -> None:
    result = utc_now()

    assert result.tzinfo is not None
    assert result.utcoffset() == UTC.utcoffset(None)
