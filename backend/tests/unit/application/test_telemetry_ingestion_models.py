"""Unit tests for the Gate F1 application command/result types.

``TelemetryIngestionCommand`` and ``TelemetryIngestionResult`` are pure
data: no persistence, no UnitOfWork, no RuleEngine call. See
``meta_rne.application.models`` for the approved shapes (Gate F0's plan,
Section 10).
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.application.models import TelemetryIngestionCommand, TelemetryIngestionResult
from meta_rne.domain.anomaly import Anomaly, CpuHighEvidence, CpuSampleEvidence, RuleId
from meta_rne.domain.telemetry import TelemetrySample

DEVICE_ID = "spine-01"
OTHER_DEVICE_ID = "leaf-02"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=20)


def _sample(device_id: str = DEVICE_ID, sampled_at: datetime = T0) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def _anomaly(device_id: str = DEVICE_ID, detected_at: datetime = T0) -> Anomaly:
    return Anomaly(
        device_id=device_id,
        rule_id=RuleId.CPU_HIGH,
        evidence=CpuHighEvidence(
            samples=(
                CpuSampleEvidence(timestamp=T0, cpu_utilization_pct=95.0),
                CpuSampleEvidence(timestamp=T1, cpu_utilization_pct=95.0),
            )
        ),
        detected_at=detected_at,
    )


def _command(**overrides: object) -> TelemetryIngestionCommand:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "sample": _sample(),
        "observed_at": T0,
    }
    defaults.update(overrides)
    return TelemetryIngestionCommand(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> TelemetryIngestionResult:
    defaults: dict[str, object] = {
        "sample": _sample(),
        "anomalies": (),
    }
    defaults.update(overrides)
    return TelemetryIngestionResult(**defaults)  # type: ignore[arg-type]


# --- TelemetryIngestionCommand ------------------------------------------


def test_command__valid_fields__constructs() -> None:
    sample = _sample()

    command = _command(sample=sample)

    assert command.device_id == DEVICE_ID
    assert command.sample is sample
    assert command.observed_at == T0


def test_command__exact_sample_object_is_retained_by_identity() -> None:
    sample = _sample()

    command = _command(sample=sample)

    assert command.sample is sample


def test_command__empty_device_id__rejected() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _command(device_id="")


def test_command__whitespace_only_device_id__rejected() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _command(device_id="   ")


def test_command__device_id_mismatch_with_sample__rejected() -> None:
    with pytest.raises(ValueError):
        _command(device_id=OTHER_DEVICE_ID, sample=_sample(device_id=DEVICE_ID))


def test_command__naive_observed_at__rejected() -> None:
    with pytest.raises(ValueError, match="observed_at"):
        _command(observed_at=datetime(2026, 7, 18, 10, 0, 0))


def test_command__non_utc_offset_observed_at__rejected() -> None:
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError, match="observed_at"):
        _command(observed_at=non_utc)


def test_command__utc_observed_at__preserved_exactly() -> None:
    command = _command(observed_at=T1)

    assert command.observed_at == T1


def test_command__future_sampled_at__accepted() -> None:
    future_sample = _sample(sampled_at=T0 + timedelta(days=1))

    command = _command(sample=future_sample, observed_at=T0)

    assert command.sample.sampled_at == T0 + timedelta(days=1)


def test_command__sampled_at_later_than_observed_at__accepted() -> None:
    later_sample = _sample(sampled_at=T1)

    command = _command(sample=later_sample, observed_at=T0)

    assert command.sample.sampled_at > command.observed_at


# --- TelemetryIngestionResult --------------------------------------------


def test_result__valid_construction_with_empty_tuple__constructs() -> None:
    sample = _sample()

    result = _result(sample=sample, anomalies=())

    assert result.sample is sample
    assert result.anomalies == ()


def test_result__valid_construction_with_anomaly_values__constructs() -> None:
    anomalies = (_anomaly(), _anomaly())

    result = _result(anomalies=anomalies)

    assert result.anomalies == anomalies


def test_result__exact_sample_object_is_retained_by_identity() -> None:
    sample = _sample()

    result = _result(sample=sample)

    assert result.sample is sample


def test_result__exact_tuple_object_is_retained_by_identity() -> None:
    anomalies = (_anomaly(),)

    result = _result(anomalies=anomalies)

    assert result.anomalies is anomalies


def test_result__list_of_anomalies__raises_type_error() -> None:
    with pytest.raises(TypeError):
        _result(anomalies=[_anomaly()])  # type: ignore[arg-type]


def test_result__non_tuple_iterable__raises_type_error() -> None:
    def _gen() -> object:
        yield _anomaly()

    with pytest.raises(TypeError):
        _result(anomalies=_gen())  # type: ignore[arg-type]


def test_result__anomaly_for_another_device__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _result(
            sample=_sample(device_id=DEVICE_ID), anomalies=(_anomaly(device_id=OTHER_DEVICE_ID),)
        )


def test_result__exact_duplicate_anomaly_values__not_deduplicated() -> None:
    anomaly = _anomaly()

    result = _result(anomalies=(anomaly, anomaly))

    assert len(result.anomalies) == 2
