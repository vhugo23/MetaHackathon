"""Repository conformance tests for TelemetryRepository (Gate E2B).

Run against both the in-memory and SQLAlchemy implementations via the
shared ``repositories`` fixture (conftest.py in this directory). Every
successful save is preceded by a valid `Device` through
`repositories.devices` for both backends, keeping setup identical while
leaving unknown-device behavior outside this shared contract (PostgreSQL
enforces it via FK; the in-memory repository stays device-existence-
agnostic by design, per the Gate E0/E1 audit).

Every case here stays under 100 samples and within one five-minute
event-time window, so the in-memory repository's retention policy (Gate
E1) never diverges from PostgreSQL's unbounded storage within the scope of
a shared test — retention/pruning itself is intentionally NOT a shared
contract case (see test_in_memory_telemetry_repository.py and
test_sqlalchemy_telemetry_repository.py for each backend's own behavior).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)

DEVICE_ID = "spine-01"
OTHER_DEVICE_ID = "leaf-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(device_id: str = DEVICE_ID) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


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


# --- Empty repository ----------------------------------------------------


def test_telemetry_repository__empty__get_latest_returns_none(
    repositories: SimpleNamespace,
) -> None:
    assert repositories.telemetry_samples.get_latest(DEVICE_ID) is None


def test_telemetry_repository__empty__get_recent_returns_empty_list(
    repositories: SimpleNamespace,
) -> None:
    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == []


# --- One-sample round trip ------------------------------------------------


def test_telemetry_repository__one_sample__get_latest_round_trips(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample()

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_telemetry_repository__one_sample__get_recent_round_trips(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample()

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [sample]


# --- Device-ID mismatch ----------------------------------------------------


def test_telemetry_repository__device_id_mismatch__raises_value_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample(device_id=DEVICE_ID)

    try:
        repositories.telemetry_samples.save(OTHER_DEVICE_ID, sample)
        raised = False
    except ValueError:
        raised = True

    assert raised


def test_telemetry_repository__device_id_mismatch__performs_no_write(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample(device_id=DEVICE_ID)

    try:
        repositories.telemetry_samples.save(OTHER_DEVICE_ID, sample)
    except ValueError:
        pass

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == []


# --- Exact duplicates / duplicate timestamps -------------------------------


def test_telemetry_repository__exact_duplicate_samples__both_retained(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample()

    repositories.telemetry_samples.save(DEVICE_ID, sample)
    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [sample, sample]


def test_telemetry_repository__duplicate_sampled_at__both_retained(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    first = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=10.0,
        memory_utilization_pct=10.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )
    second = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=20.0,
        memory_utilization_pct=20.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )

    repositories.telemetry_samples.save(DEVICE_ID, first)
    repositories.telemetry_samples.save(DEVICE_ID, second)

    result = repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert len(result) == 2
    assert first in result
    assert second in result


def test_telemetry_repository__equal_timestamps__get_recent_preserves_identity_order(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    first = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=10.0,
        memory_utilization_pct=10.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )
    second = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=20.0,
        memory_utilization_pct=20.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )

    repositories.telemetry_samples.save(DEVICE_ID, first)
    repositories.telemetry_samples.save(DEVICE_ID, second)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [first, second]


def test_telemetry_repository__equal_timestamps__get_latest_is_later_saved_sample(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    first = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=10.0,
        memory_utilization_pct=10.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )
    second = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=20.0,
        memory_utilization_pct=20.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )

    repositories.telemetry_samples.save(DEVICE_ID, first)
    repositories.telemetry_samples.save(DEVICE_ID, second)

    assert repositories.telemetry_samples.get_latest(DEVICE_ID) == second


# --- Out-of-order saves ------------------------------------------------------


def test_telemetry_repository__out_of_order_save__accepted(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repositories.telemetry_samples.save(DEVICE_ID, later)
    repositories.telemetry_samples.save(DEVICE_ID, earlier)


def test_telemetry_repository__out_of_order_save__get_latest_uses_greatest_sampled_at(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repositories.telemetry_samples.save(DEVICE_ID, later)
    repositories.telemetry_samples.save(DEVICE_ID, earlier)

    assert repositories.telemetry_samples.get_latest(DEVICE_ID) == later


def test_telemetry_repository__out_of_order_save__get_recent_is_sampled_at_ascending(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repositories.telemetry_samples.save(DEVICE_ID, later)
    repositories.telemetry_samples.save(DEVICE_ID, earlier)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [earlier, later]


# --- since filtering -----------------------------------------------------------


def test_telemetry_repository__since_boundary_is_inclusive(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample(sampled_at=T0)

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [sample]


def test_telemetry_repository__samples_older_than_since_are_excluded(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    old = _sample(sampled_at=T0)
    new = _sample(sampled_at=T0 + timedelta(seconds=30))
    repositories.telemetry_samples.save(DEVICE_ID, old)
    repositories.telemetry_samples.save(DEVICE_ID, new)

    result = repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0 + timedelta(seconds=1))

    assert result == [new]


# --- Device isolation ------------------------------------------------------------


def test_telemetry_repository__different_devices_remain_isolated(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device(DEVICE_ID))
    repositories.devices.save(_device(OTHER_DEVICE_ID))
    device_a_sample = _sample(device_id=DEVICE_ID, sampled_at=T0)
    device_b_sample = _sample(device_id=OTHER_DEVICE_ID, sampled_at=T0)

    repositories.telemetry_samples.save(DEVICE_ID, device_a_sample)
    repositories.telemetry_samples.save(OTHER_DEVICE_ID, device_b_sample)

    assert repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [device_a_sample]
    assert repositories.telemetry_samples.get_recent(OTHER_DEVICE_ID, since=T0) == [device_b_sample]


# --- Nested value round-trip -----------------------------------------------------


def test_telemetry_repository__interface_states_and_bgp_sessions_round_trip_exactly(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=91.5,
        memory_utilization_pct=42.5,
        interface_error_rate=0.02,
        interface_states=(
            InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),
            InterfaceState(name="GigabitEthernet0/2", oper_state=LinkState.DOWN),
        ),
        bgp_sessions=(
            BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),
            BgpSession(neighbor_ip="10.0.0.2", state=BgpState.IDLE),
        ),
    )

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_telemetry_repository__duplicate_interface_names_and_neighbor_ips_survive(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(
            InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),
            InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.DOWN),
        ),
        bgp_sessions=(
            BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),
            BgpSession(neighbor_ip="10.0.0.1", state=BgpState.IDLE),
        ),
    )

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert repositories.telemetry_samples.get_latest(DEVICE_ID) == sample


# --- Fresh list / non-mutation ----------------------------------------------------


def test_telemetry_repository__get_recent_returns_newly_allocated_list_each_call(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample()
    repositories.telemetry_samples.save(DEVICE_ID, sample)

    first_call = repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0)
    second_call = repositories.telemetry_samples.get_recent(DEVICE_ID, since=T0)

    assert first_call == second_call
    assert first_call is not second_call


def test_telemetry_repository__save_and_retrieve_does_not_mutate_input_sample(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    sample = _sample()

    repositories.telemetry_samples.save(DEVICE_ID, sample)

    assert sample == _sample()
