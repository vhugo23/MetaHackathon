"""Unit tests for the Day 5A application command/result types.

``IngestConfigurationCommand`` and ``ConfigIngestionResult`` are pure data:
no adapter resolution, no persistence, no UnitOfWork. See
``meta_rne.application.models`` for the approved shapes (Day 5A plan,
item 1/2).
"""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.application.models import ConfigIngestionResult, IngestConfigurationCommand
from meta_rne.domain.config import (
    NormalizedConfiguration,
    NormalizedRouting,
)

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _empty_normalized_config() -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname="spine-01",
        interfaces=(),
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=(),
    )


def _command(**overrides: object) -> IngestConfigurationCommand:
    defaults: dict[str, object] = {
        "device_id": "spine-01",
        "vendor": "cisco-ios-xe",
        "raw_config_text": "hostname spine-01\n",
        "observed_at": T0,
    }
    defaults.update(overrides)
    return IngestConfigurationCommand(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> ConfigIngestionResult:
    defaults: dict[str, object] = {
        "device_id": "spine-01",
        "snapshot_id": "snap-1",
        "normalized_config": _empty_normalized_config(),
        "violations_detected": 1,
        "incidents_created": 1,
        "incidents_updated": 0,
    }
    defaults.update(overrides)
    return ConfigIngestionResult(**defaults)  # type: ignore[arg-type]


# --- IngestConfigurationCommand -----------------------------------------


def test_command__valid_fields__constructs() -> None:
    command = _command()

    assert command.device_id == "spine-01"
    assert command.vendor == "cisco-ios-xe"
    assert command.raw_config_text == "hostname spine-01\n"
    assert command.observed_at == T0


def test_command__blank_device_id__rejected() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _command(device_id="   ")


def test_command__blank_vendor__rejected() -> None:
    with pytest.raises(ValueError, match="vendor"):
        _command(vendor="")


def test_command__empty_raw_config_text__rejected() -> None:
    with pytest.raises(ValueError, match="raw_config_text"):
        _command(raw_config_text="")


def test_command__whitespace_containing_raw_text__is_preserved_exactly() -> None:
    raw_text = "hostname spine-01\n   \ninterface Gi0/1\n\tdescription x\n"

    command = _command(raw_config_text=raw_text)

    assert command.raw_config_text == raw_text


def test_command__naive_observed_at__rejected() -> None:
    with pytest.raises(ValueError, match="observed_at"):
        _command(observed_at=datetime(2026, 7, 18, 10, 0, 0))


def test_command__non_utc_observed_at__rejected() -> None:
    non_utc_time = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError, match="observed_at"):
        _command(observed_at=non_utc_time)


def test_command__is_frozen_and_slotted() -> None:
    command = _command()

    with pytest.raises(dataclasses.FrozenInstanceError):
        command.device_id = "other"  # type: ignore[misc]

    assert not hasattr(command, "__dict__")


# --- ConfigIngestionResult ------------------------------------------------


def test_result__valid_fields__constructs() -> None:
    result = _result()

    assert result.device_id == "spine-01"
    assert result.snapshot_id == "snap-1"
    assert result.violations_detected == 1
    assert result.incidents_created == 1
    assert result.incidents_updated == 0


def test_result__blank_device_id__rejected() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _result(device_id=" ")


def test_result__blank_snapshot_id__rejected() -> None:
    with pytest.raises(ValueError, match="snapshot_id"):
        _result(snapshot_id="")


def test_result__negative_violations_detected__rejected() -> None:
    with pytest.raises(ValueError, match="violations_detected"):
        _result(violations_detected=-1, incidents_created=0, incidents_updated=0)


def test_result__negative_incidents_created__rejected() -> None:
    with pytest.raises(ValueError, match="incidents_created"):
        _result(incidents_created=-1, violations_detected=0, incidents_updated=0)


def test_result__negative_incidents_updated__rejected() -> None:
    with pytest.raises(ValueError, match="incidents_updated"):
        _result(incidents_updated=-1, violations_detected=0, incidents_created=0)


def test_result__created_plus_updated_mismatch_with_violations__rejected() -> None:
    with pytest.raises(ValueError, match="violations_detected"):
        _result(violations_detected=2, incidents_created=1, incidents_updated=0)


def test_result__zero_violations_zero_incidents__constructs() -> None:
    result = _result(violations_detected=0, incidents_created=0, incidents_updated=0)

    assert result.violations_detected == 0


def test_result__is_frozen_and_slotted() -> None:
    result = _result()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.device_id = "other"  # type: ignore[misc]

    assert not hasattr(result, "__dict__")
