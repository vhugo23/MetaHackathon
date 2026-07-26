"""TelemetrySample/InterfaceState/BgpSession domain value object invariants
(Gate A — telemetry domain values only).

Pure construction-time invariants only. `RuleEngine`, `Anomaly`, telemetry
ingestion, and persistence are later gates — see docs/domain-model.md
Section 8 and docs/product-spec.md FR-05.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)

DEVICE_ID = "spine-01"
SAMPLED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _sample(**overrides: object) -> TelemetrySample:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "sampled_at": SAMPLED_AT,
        "cpu_utilization_pct": 42.0,
        "memory_utilization_pct": 55.0,
        "interface_error_rate": 0.0,
        "interface_states": (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),),
        "bgp_sessions": (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),),
    }
    defaults.update(overrides)
    return TelemetrySample(**defaults)  # type: ignore[arg-type]


# --- Enum contracts ---------------------------------------------------


def test_link_state__has_approved_members_only() -> None:
    assert {member.value for member in LinkState} == {"up", "down"}


def test_bgp_state__has_approved_members_only() -> None:
    assert {member.value for member in BgpState} == {
        "Idle",
        "Connect",
        "Active",
        "OpenSent",
        "OpenConfirm",
        "Established",
    }


# --- Nested value objects ----------------------------------------------


def test_interface_state__valid_fields__preserves_name_and_oper_state() -> None:
    state = InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.DOWN)

    assert state.name == "GigabitEthernet0/1"
    assert state.oper_state == LinkState.DOWN


def test_bgp_session__valid_fields__preserves_neighbor_ip_and_state() -> None:
    session = BgpSession(neighbor_ip="10.0.0.1", state=BgpState.IDLE)

    assert session.neighbor_ip == "10.0.0.1"
    assert session.state == BgpState.IDLE


def test_interface_state__is_immutable() -> None:
    state = InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP)

    with pytest.raises(AttributeError):
        state.name = "GigabitEthernet0/2"  # type: ignore[misc]


def test_bgp_session__is_immutable() -> None:
    session = BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED)

    with pytest.raises(AttributeError):
        session.neighbor_ip = "10.0.0.2"  # type: ignore[misc]


# --- Valid TelemetrySample ----------------------------------------------


def test_telemetry_sample__valid_fields__preserves_every_field_exactly() -> None:
    interface_states = (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),)
    bgp_sessions = (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),)

    sample = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=SAMPLED_AT,
        cpu_utilization_pct=42.5,
        memory_utilization_pct=55.5,
        interface_error_rate=0.02,
        interface_states=interface_states,
        bgp_sessions=bgp_sessions,
    )

    assert sample.device_id == DEVICE_ID
    assert sample.sampled_at == SAMPLED_AT
    assert sample.cpu_utilization_pct == 42.5
    assert sample.memory_utilization_pct == 55.5
    assert sample.interface_error_rate == 0.02
    assert sample.interface_states == interface_states
    assert sample.bgp_sessions == bgp_sessions


def test_telemetry_sample__zero_cpu_and_memory__accepted() -> None:
    sample = _sample(cpu_utilization_pct=0.0, memory_utilization_pct=0.0)

    assert sample.cpu_utilization_pct == 0.0
    assert sample.memory_utilization_pct == 0.0


def test_telemetry_sample__cpu_and_memory_at_100__accepted() -> None:
    sample = _sample(cpu_utilization_pct=100.0, memory_utilization_pct=100.0)

    assert sample.cpu_utilization_pct == 100.0
    assert sample.memory_utilization_pct == 100.0


def test_telemetry_sample__empty_interface_and_bgp_tuples__accepted() -> None:
    sample = _sample(interface_states=(), bgp_sessions=())

    assert sample.interface_states == ()
    assert sample.bgp_sessions == ()


def test_telemetry_sample__utc_aware_sampled_at__accepted() -> None:
    sample = _sample(sampled_at=SAMPLED_AT)

    assert sample.sampled_at == SAMPLED_AT


def test_telemetry_sample__is_immutable() -> None:
    sample = _sample()

    with pytest.raises(AttributeError):
        sample.device_id = "leaf-01"  # type: ignore[misc]


# --- Device ID validation ------------------------------------------------


def test_telemetry_sample__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _sample(device_id="")


def test_telemetry_sample__whitespace_only_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _sample(device_id="   ")


# --- CPU validation --------------------------------------------------------


def test_telemetry_sample__cpu_below_zero__raises_value_error() -> None:
    with pytest.raises(ValueError, match="cpu_utilization_pct"):
        _sample(cpu_utilization_pct=-0.1)


def test_telemetry_sample__cpu_above_100__raises_value_error() -> None:
    with pytest.raises(ValueError, match="cpu_utilization_pct"):
        _sample(cpu_utilization_pct=100.1)


# --- Memory validation -------------------------------------------------------


def test_telemetry_sample__memory_below_zero__raises_value_error() -> None:
    with pytest.raises(ValueError, match="memory_utilization_pct"):
        _sample(memory_utilization_pct=-0.1)


def test_telemetry_sample__memory_above_100__raises_value_error() -> None:
    with pytest.raises(ValueError, match="memory_utilization_pct"):
        _sample(memory_utilization_pct=100.1)


# --- Timestamp validation ----------------------------------------------------


def test_telemetry_sample__naive_sampled_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="sampled_at"):
        _sample(sampled_at=datetime(2026, 7, 18, 10, 0, 0))


def test_telemetry_sample__non_utc_offset_sampled_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="sampled_at"):
        _sample(sampled_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))))
