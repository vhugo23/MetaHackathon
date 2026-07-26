"""Contract tests for ``POST /devices/{device_id}/telemetry`` (Gate G1).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides`` — same
convention as ``test_config_ingestion_api.py``/``test_device_drift_api.py``.

Requests submit raw JSON payloads (dicts) rather than importing the
not-yet-existing response schema classes, so the expected-red result is an
HTTP routing failure (404, unregistered path), never an import error.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.observability import IncidentLogEvent, StdoutIncidentEventSink
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

_EXPECTED_EVENT_KEY_ORDER = [
    "incident_id",
    "device_id",
    "rule_ref",
    "severity",
    "status",
    "outcome",
    "timestamp",
]


def _json_lines(stdout: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


class _CountingClock:
    def __init__(self, value: datetime) -> None:
        self._value = value
        self.call_count = 0

    def __call__(self) -> datetime:
        self.call_count += 1
        return self._value


def _test_app(
    store: InMemoryStore,
    *,
    clock: object = lambda: T0,
) -> TestClient:
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


def _payload(**overrides: object) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "sampled_at": T0.isoformat(),
        "cpu_utilization_pct": 50.0,
        "memory_utilization_pct": 50.0,
        "interface_error_rate": 0.0,
        "interface_states": [],
        "bgp_sessions": [],
    }
    defaults.update(overrides)
    return defaults


# --- 1. Route existence -----------------------------------------------------


def test_openapi_document_includes_telemetry_route() -> None:
    store = InMemoryStore()
    client = _test_app(store)

    schema = client.get("/openapi.json").json()

    assert "/devices/{device_id}/telemetry" in schema["paths"]
    assert "post" in schema["paths"]["/devices/{device_id}/telemetry"]


# --- 2-8. Success shape ------------------------------------------------------


def test_valid_sample__returns_201() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    assert response.status_code == 201


def test_response_has_exactly_sample_and_anomalies() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    assert set(response.json().keys()) == {"sample", "anomalies"}


def test_zero_anomaly_response__returns_sample_and_empty_anomalies() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    body = response.json()
    assert body["anomalies"] == []
    assert body["sample"]["device_id"] == DEVICE_ID


def test_sample_response_echoes_exact_fields() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    payload = _payload(
        cpu_utilization_pct=42.5,
        memory_utilization_pct=33.3,
        interface_error_rate=0.01,
        interface_states=[{"name": "GigabitEthernet0/1", "oper_state": "up"}],
        bgp_sessions=[{"neighbor_ip": "10.0.0.2", "state": "Established"}],
    )

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)

    sample = response.json()["sample"]
    assert sample["device_id"] == DEVICE_ID
    assert datetime.fromisoformat(sample["sampled_at"]) == T0
    assert sample["cpu_utilization_pct"] == 42.5
    assert sample["memory_utilization_pct"] == 33.3
    assert sample["interface_error_rate"] == 0.01
    assert sample["interface_states"] == [{"name": "GigabitEthernet0/1", "oper_state": "up"}]
    assert sample["bgp_sessions"] == [{"neighbor_ip": "10.0.0.2", "state": "Established"}]


def test_empty_interface_and_bgp_collections__serialize_as_arrays() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    sample = response.json()["sample"]
    assert sample["interface_states"] == []
    assert sample["bgp_sessions"] == []


def test_interface_states__preserve_request_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    payload = _payload(
        interface_states=[
            {"name": "Eth1", "oper_state": "down"},
            {"name": "Eth2", "oper_state": "up"},
            {"name": "Eth3", "oper_state": "down"},
        ]
    )

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)

    names = [entry["name"] for entry in response.json()["sample"]["interface_states"]]
    assert names == ["Eth1", "Eth2", "Eth3"]


def test_bgp_sessions__preserve_request_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    payload = _payload(
        bgp_sessions=[
            {"neighbor_ip": "10.0.0.1", "state": "Established"},
            {"neighbor_ip": "10.0.0.2", "state": "Idle"},
            {"neighbor_ip": "10.0.0.3", "state": "Active"},
        ]
    )

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)

    ips = [entry["neighbor_ip"] for entry in response.json()["sample"]["bgp_sessions"]]
    assert ips == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


# --- 9-12. Anomaly triggering -------------------------------------------------


def test_two_consecutive_cpu_high_submissions__second_response_includes_cpu_high() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=(T0.replace(second=30)).isoformat(), cpu_utilization_pct=95.0),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-CPU-HIGH" in rule_ids


def test_five_observation_link_flap_sequence__final_response_includes_link_flap() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    interface = "GigabitEthernet0/1"
    states = ["up", "down", "up", "down"]

    for offset, state in enumerate(states):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_payload(
                sampled_at=T0.replace(second=12 * offset).isoformat(),
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=48).isoformat(),
            interface_states=[{"name": interface, "oper_state": "up"}],
        ),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-LINK-FLAP" in rule_ids


def test_established_then_idle__second_response_includes_bgp_down() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    neighbor = "10.0.0.2"

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=30).isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-BGP-DOWN" in rule_ids


def test_all_three_rules_trigger__anomaly_order_is_cpu_flap_bgp() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"

    for offset, state in enumerate(["up", "down", "up", "down"]):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_payload(
                sampled_at=T0.replace(second=10 * offset).isoformat(),
                cpu_utilization_pct=95.0,
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=40).isoformat(),
            cpu_utilization_pct=95.0,
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=50).isoformat(),
            cpu_utilization_pct=95.0,
            interface_states=[{"name": interface, "oper_state": "up"}],
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert rule_ids.index("RULE-CPU-HIGH") < rule_ids.index("RULE-LINK-FLAP")
    assert rule_ids.index("RULE-LINK-FLAP") < rule_ids.index("RULE-BGP-DOWN")


# --- 13. Missing device ------------------------------------------------------


def test_missing_device__returns_exact_404_body() -> None:
    store = InMemoryStore()
    client = _test_app(store)

    response = client.post("/devices/missing-device/telemetry", json=_payload())

    assert response.status_code == 404
    assert response.json() == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


# --- 14-22. Validation --------------------------------------------------------


def test_cpu_below_zero__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(cpu_utilization_pct=-1.0)
    )

    assert response.status_code == 422


def test_cpu_above_100__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(cpu_utilization_pct=101.0)
    )

    assert response.status_code == 422


def test_memory_below_zero__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(memory_utilization_pct=-1.0)
    )

    assert response.status_code == 422


def test_memory_above_100__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(memory_utilization_pct=101.0)
    )

    assert response.status_code == 422


def test_naive_sampled_at__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(sampled_at="2026-07-18T10:00:00")
    )

    assert response.status_code == 422


def test_non_utc_sampled_at__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at="2026-07-18T10:00:00+02:00"),
    )

    assert response.status_code == 422


def test_invalid_link_state__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(interface_states=[{"name": "Eth1", "oper_state": "unknown"}]),
    )

    assert response.status_code == 422


def test_invalid_bgp_state__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(bgp_sessions=[{"neighbor_ip": "10.0.0.2", "state": "unknown"}]),
    )

    assert response.status_code == 422


def test_unexpected_request_body_field__rejected_with_422() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry", json=_payload(unexpected_field="boom")
    )

    assert response.status_code == 422


# --- 23. Exact-duplicate submissions ------------------------------------------


def test_exact_duplicate_submission__accepted_with_201() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    payload = _payload()

    first = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)
    second = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201


# --- 24-25. Clock / observed_at behavior --------------------------------------


def test_api_clock_is_called_exactly_once_per_request() -> None:
    store = InMemoryStore()
    _seed_device(store)
    clock = _CountingClock(T0)
    client = _test_app(store, clock=clock)

    client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    assert clock.call_count == 1


def test_observed_at_is_not_accepted_in_request_body() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(observed_at=T0.isoformat()),
    )

    assert response.status_code == 422


# --- 26. No incident-shaped fields ---------------------------------------------


def test_response_contains_no_incident_shaped_fields() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

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


# --- AC-10E: structured incident events through real HTTP + real stdout -----


def test_two_consecutive_cpu_high_submissions__second_request_emits_one_created_event(
    capfd: pytest.CaptureFixture[str],
) -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    capfd.readouterr()  # discard the first request's own captured output (no anomaly yet)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=(T0.replace(second=30)).isoformat(), cpu_utilization_pct=95.0),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    body = response.json()
    rule_ids = [a["rule_id"] for a in body["anomalies"]]
    assert "RULE-CPU-HIGH" in rule_ids

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert list(event.keys()) == _EXPECTED_EVENT_KEY_ORDER
    assert event["rule_ref"] == "RULE-CPU-HIGH"
    assert event["outcome"] == "CREATED"
    assert event["severity"] == "High"
    assert event["status"] == "OPEN"

    verify_uow = InMemoryUnitOfWork(store)
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    incident = incidents[0]
    assert event["incident_id"] == incident.incident_id
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_repeated_cpu_high_detection__third_request_emits_one_updated_event(
    capfd: pytest.CaptureFixture[str],
) -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store, clock=lambda: T0)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.replace(second=30).isoformat(), cpu_utilization_pct=95.0),
    )
    capfd.readouterr()  # discard everything captured before the repeated-detection request

    t1 = T0 + timedelta(hours=1)
    client2 = _test_app(store, clock=lambda: t1)
    response = client2.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=(T0 + timedelta(seconds=60)).isoformat(), cpu_utilization_pct=95.0
        ),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert set(response.json().keys()) == {"sample", "anomalies"}

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert event["outcome"] == "UPDATED"

    verify_uow = InMemoryUnitOfWork(store)
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.occurrence_count == 2
    assert incident.created_at == T0
    assert incident.last_seen_at == t1
    assert event["incident_id"] == incident.incident_id
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_zero_anomaly_response__emits_no_stdout_event(capfd: pytest.CaptureFixture[str]) -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    capfd.readouterr()
    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert response.json()["anomalies"] == []
    assert captured.out == ""

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.telemetry_samples.get_latest(DEVICE_ID) is not None


def test_all_three_rules_trigger__final_request_emits_three_ordered_events(
    capfd: pytest.CaptureFixture[str],
) -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"

    for offset, state in enumerate(["up", "down", "up", "down"]):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_payload(
                sampled_at=T0.replace(second=10 * offset).isoformat(),
                cpu_utilization_pct=50.0,
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=40).isoformat(),
            cpu_utilization_pct=95.0,
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    capfd.readouterr()  # discard everything captured before the firing request

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=50).isoformat(),
            cpu_utilization_pct=95.0,
            interface_states=[{"name": interface, "oper_state": "up"}],
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert set(response.json().keys()) == {"sample", "anomalies"}

    events = _json_lines(captured.out)
    assert len(events) == 3
    assert [event["rule_ref"] for event in events] == [
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    ]
    assert all(event["outcome"] == "CREATED" for event in events)

    # Real ordering is asserted above from the event stream itself; durable
    # verification maps each event to its incident by incident_id rather
    # than trusting `list_all()`'s positional order for tied timestamps.
    verify_uow = InMemoryUnitOfWork(store)
    incidents_by_id = {
        incident.incident_id: incident for incident in verify_uow.incidents.list_all()
    }
    assert {event["incident_id"] for event in events} == set(incidents_by_id)
    for event in events:
        persisted = incidents_by_id[event["incident_id"]]
        assert event["rule_ref"] == persisted.rule_ref
        assert event["device_id"] == DEVICE_ID
        assert event["status"] == "OPEN"
        assert event["timestamp"] == persisted.last_seen_at.isoformat().replace("+00:00", "Z")


def test_cpu_high_submission__sink_emit_raises__response_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raising_emit(self: StdoutIncidentEventSink, event: IncidentLogEvent) -> None:
        raise RuntimeError("sink boom")

    monkeypatch.setattr(StdoutIncidentEventSink, "emit", _raising_emit)

    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.replace(second=30).isoformat(), cpu_utilization_pct=95.0),
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"sample", "anomalies"}
    rule_ids = [a["rule_id"] for a in body["anomalies"]]
    assert "RULE-CPU-HIGH" in rule_ids

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.telemetry_samples.get_latest(DEVICE_ID) is not None
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
