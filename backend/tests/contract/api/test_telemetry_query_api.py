"""Contract tests for ``GET /devices/{device_id}/telemetry/recent`` (Gate
G2C). Each test builds its own isolated ``create_app(...)`` instance —
never the module-level production ``app`` — same convention as
``test_telemetry_ingestion_api.py``/``test_device_drift_api.py``.

Requests submit raw HTTP query parameters and read responses via
``response.json()``/``response.text`` only — no future schema or route
function is imported.
"""

from datetime import UTC, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


class _CountingClock:
    def __init__(self, value: datetime) -> None:
        self._value = value
        self.call_count = 0

    def __call__(self) -> datetime:
        self.call_count += 1
        return self._value


def _test_app(store: InMemoryStore, *, clock: object = lambda: T0) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        clock=clock,  # type: ignore[arg-type]
        seed_on_startup=False,
    )
    return TestClient(app)


def _seed_device(store: InMemoryStore, device_id: str = DEVICE_ID) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.devices.save(
        Device(
            device_id=device_id,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    uow.commit()


def _seed_sample(store: InMemoryStore, sample: TelemetrySample) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()


def _sample(
    device_id: str = DEVICE_ID,
    sampled_at: datetime = T0,
    interface_states: tuple[InterfaceState, ...] = (),
    bgp_sessions: tuple[BgpSession, ...] = (),
) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=interface_states,
        bgp_sessions=bgp_sessions,
    )


# --- 1. Route existence -----------------------------------------------------


def test_openapi_document_includes_recent_telemetry_route() -> None:
    store = InMemoryStore()
    client = _test_app(store)

    schema = client.get("/openapi.json").json()

    assert "/devices/{device_id}/telemetry/recent" in schema["paths"]
    assert "get" in schema["paths"]["/devices/{device_id}/telemetry/recent"]


# --- 2-5. Success shape ------------------------------------------------------


def test_existing_device_with_no_matching_telemetry__returns_200() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code == 200


def test_empty_history__returns_bare_empty_array() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.json() == []


def test_one_matching_sample__returns_bare_one_element_array() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1


def test_response_is_not_wrapped_in_an_object() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert isinstance(response.json(), list)


# --- 6-9. Sample shape / nested collections ----------------------------------


def test_sample_contains_exactly_the_approved_fields() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert set(response.json()[0].keys()) == {
        "device_id",
        "sampled_at",
        "cpu_utilization_pct",
        "memory_utilization_pct",
        "interface_error_rate",
        "interface_states",
        "bgp_sessions",
    }


def test_nested_interface_states__serialize_correctly() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(
        store,
        _sample(interface_states=(InterfaceState(name="Eth1", oper_state=LinkState.UP),)),
    )
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.json()[0]["interface_states"] == [{"name": "Eth1", "oper_state": "up"}]


def test_nested_bgp_sessions__serialize_correctly() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(
        store,
        _sample(bgp_sessions=(BgpSession(neighbor_ip="10.0.0.2", state=BgpState.ESTABLISHED),)),
    )
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.json()[0]["bgp_sessions"] == [
        {"neighbor_ip": "10.0.0.2", "state": "Established"}
    ]


def test_empty_nested_collections__serialize_as_arrays() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.json()[0]["interface_states"] == []
    assert response.json()[0]["bgp_sessions"] == []


# --- 10-14. Ordering / boundary / duplicates / future rows -------------------


def test_sample_exactly_equal_to_since__is_included() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0))
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 1


def test_samples_returned_in_ascending_sampled_at_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    for offset in (30, 0, 20, 10):
        _seed_sample(store, _sample(sampled_at=T0 + timedelta(seconds=offset)))
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    timestamps = [entry["sampled_at"] for entry in response.json()]
    parsed = [datetime.fromisoformat(t) for t in timestamps]
    assert parsed == sorted(parsed)


def test_equal_timestamps__preserve_repository_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    direct_uow = InMemoryUnitOfWork(store)
    for _ in range(3):
        direct_uow.telemetry_samples.save(DEVICE_ID, _sample(sampled_at=T0))
    direct_uow.commit()
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 3


def test_exact_duplicate_rows__both_returned() -> None:
    store = InMemoryStore()
    _seed_device(store)
    duplicate = _sample(sampled_at=T0)
    _seed_sample(store, duplicate)
    _seed_sample(store, duplicate)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 2


def test_future_dated_row__returned_when_at_or_after_since() -> None:
    store = InMemoryStore()
    _seed_device(store)
    future_sample = _sample(sampled_at=T0 + timedelta(hours=1))
    _seed_sample(store, future_sample)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 1


# --- 15-18. since validation --------------------------------------------------


def test_missing_since__returns_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/telemetry/recent")

    assert response.status_code == 422


def test_malformed_since__returns_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": "not-a-timestamp"}
    )

    assert response.status_code == 422


def test_naive_since__returns_existing_exact_422_invalid_request_body() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent",
        params={"since": "2026-07-18T10:00:00"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "since must be timezone-aware UTC",
    }


def test_non_utc_offset_since__returns_existing_exact_422_invalid_request_body() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": non_utc.isoformat()}
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "since must be timezone-aware UTC",
    }


# --- 19-20. Missing device ----------------------------------------------------


def test_missing_device_with_valid_since__returns_existing_exact_404_body() -> None:
    store = InMemoryStore()
    client = _test_app(store)

    response = client.get(
        "/devices/missing-device/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


def test_missing_device__does_not_become_empty_200() -> None:
    store = InMemoryStore()
    client = _test_app(store)

    response = client.get(
        "/devices/missing-device/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code != 200


# --- 21. API clock -------------------------------------------------------------


def test_api_clock_is_not_called() -> None:
    store = InMemoryStore()
    _seed_device(store)
    clock = _CountingClock(T0)
    client = _test_app(store, clock=clock)

    client.get(f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()})

    assert clock.call_count == 0


# --- 22-23. No leaked identity / no incident fields ---------------------------


def test_no_persistence_identity_or_sequence_metadata_appears() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    body_text = response.text
    for forbidden in ("insertion_sequence", '"id"'):
        assert forbidden not in body_text


def test_no_incident_shaped_fields_appear() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    client = _test_app(store)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    body_text = response.text
    for forbidden in (
        "incident_id",
        "fingerprint",
        "severity",
        "recommendation",
        "occurrence_count",
        "status",
    ):
        assert f'"{forbidden}"' not in body_text


# --- 24-25. Method / path behavior --------------------------------------------


def test_unsupported_method__remains_framework_controlled_405() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code == 405


def test_uses_actual_path_device_id_not_a_fixed_device() -> None:
    store = InMemoryStore()
    _seed_device(store, device_id="leaf-02")
    _seed_sample(store, _sample(device_id="leaf-02"))
    client = _test_app(store)

    response = client.get("/devices/leaf-02/telemetry/recent", params={"since": T0.isoformat()})

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["device_id"] == "leaf-02"
