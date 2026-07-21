"""Unit tests for the production clock default (Day 5B) and the
``CallableClock`` adapter to the application layer's ``Clock`` protocol
(Day 7A, Gate 7A-C)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.api.clock import CallableClock, InvalidClockError, utc_now

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def test_utc_now__returns_a_timezone_aware_utc_datetime() -> None:
    result = utc_now()

    assert result.tzinfo is not None
    assert result.utcoffset() == UTC.utcoffset(None)


def test_callable_clock__now__returns_the_wrapped_callables_value() -> None:
    clock = CallableClock(lambda: T0)

    assert clock.now() == T0


def test_callable_clock__now__calls_the_wrapped_callable_each_time() -> None:
    calls: list[int] = []

    def counting_clock() -> datetime:
        calls.append(1)
        return T0

    clock = CallableClock(counting_clock)

    clock.now()
    clock.now()

    assert len(calls) == 2


def test_callable_clock__naive_value__raises_invalid_clock_error() -> None:
    clock = CallableClock(lambda: datetime(2026, 7, 18, 10, 0, 0))

    with pytest.raises(InvalidClockError):
        clock.now()


def test_callable_clock__non_utc_offset_value__raises_invalid_clock_error() -> None:
    clock = CallableClock(
        lambda: datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    )

    with pytest.raises(InvalidClockError):
        clock.now()
