#!/usr/bin/env python3
"""Unit tests for scripts/telemetry_simulator.py.

Standard-library-plus-pytest only (matching scripts/test_browser_e2e.py's
own sys.path-based import of a sibling script, but using plain pytest
functions rather than unittest.TestCase). Never makes a real network call,
never sleeps in real time, never starts Docker, never touches PostgreSQL —
every HTTP interaction is driven through a fake client satisfying
telemetry_simulator.HttpClientLike. Tests the simulator's own payload
construction, scenario logic, CLI/exit-code behavior, and health-wait
polling — never RuleEngine correctness, which is already proven by the
backend's own extensive test suite.

Run directly:
    python -m pytest scripts/test_telemetry_simulator.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import telemetry_simulator as sim  # noqa: E402

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


# --- Fakes -------------------------------------------------------------


@dataclass
class _ScriptedHttpClient:
    """Returns pre-programmed responses/exceptions in call order — never
    touches a real socket. Satisfies telemetry_simulator.HttpClientLike."""

    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> sim.HttpResult:
        self.calls.append((method, url, json_body))
        if not self.responses:
            raise AssertionError("_ScriptedHttpClient ran out of programmed responses")
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, sim.HttpResult)
        return outcome


def _ok(status: int, body: Any) -> sim.HttpResult:
    return sim.HttpResult(status=status, body=body, method="", url="")


def _config_response(device_id: str) -> sim.HttpResult:
    return _ok(
        201,
        {
            "device_id": device_id,
            "snapshot_id": "snap-1",
            "normalized_config": {},
            "violations_detected": 0,
            "incidents_created": 0,
            "incidents_updated": 0,
        },
    )


def _telemetry_response(device_id: str, rule_ids: list[str]) -> sim.HttpResult:
    return _ok(
        201,
        {
            "sample": {"device_id": device_id},
            "anomalies": [{"rule_id": rule_id} for rule_id in rule_ids],
        },
    )


def _incident(
    incident_id: str,
    device_id: str,
    rule_ref: str,
    *,
    status: str = "OPEN",
    occurrence_count: int = 1,
    created_at: str = "2026-07-18T10:00:00Z",
    last_seen_at: str = "2026-07-18T10:00:00Z",
    fingerprint: str = "fp",
) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "device_id": device_id,
        "rule_ref": rule_ref,
        "status": status,
        "occurrence_count": occurrence_count,
        "created_at": created_at,
        "last_seen_at": last_seen_at,
        "fingerprint": fingerprint,
    }


def _incidents_response(incidents: list[dict[str, Any]]) -> sim.HttpResult:
    return _ok(200, incidents)


def _health_response() -> sim.HttpResult:
    return _ok(200, {"status": "ok"})


def _resolve_response(incident: dict[str, Any]) -> sim.HttpResult:
    resolved = dict(incident)
    resolved["status"] = "RESOLVED"
    return _ok(200, resolved)


def _body(call: tuple[str, str, dict[str, Any] | None]) -> dict[str, Any]:
    _, _, json_body = call
    assert json_body is not None
    return json_body


def _make_ctx(
    client: sim.HttpClientLike, device_id: str = "sim-test-scenario"
) -> sim.ScenarioContext:
    logs: list[str] = []
    return sim.ScenarioContext(
        client=client, base_url="http://fake", device_id=device_id, log=logs.append
    )


# 1. CLI scenario choices include all required names.
def test_scenario_names_include_all_required_names() -> None:
    assert sim.SCENARIO_NAMES == [
        "normal",
        "cpu-high",
        "cpu-update",
        "link-flap",
        "bgp-down",
        "all-anomalies",
        "resolved-recurrence",
    ]
    parser = sim.build_arg_parser()
    scenario_action = next(a for a in parser._actions if a.dest == "scenario")
    assert scenario_action.choices == [*sim.SCENARIO_NAMES, "suite"]


# 2. `suite` expands in the required deterministic order.
def test_suite_expands_in_declared_order() -> None:
    assert list(sim.SCENARIO_RUNNERS.keys()) == sim.SCENARIO_NAMES


# 3. unique run ID produces separate device IDs per scenario.
def test_generate_run_id__produces_unique_values() -> None:
    first = sim.generate_run_id(now=datetime(2026, 7, 26, 16, 5, 0, tzinfo=UTC))
    second = sim.generate_run_id(now=datetime(2026, 7, 26, 16, 5, 1, tzinfo=UTC))
    assert first != second
    device_a = sim.derive_device_id(first, "cpu-high")
    device_b = sim.derive_device_id(first, "link-flap")
    assert device_a != device_b
    assert device_a.startswith(f"sim-{first}-")


# 4. explicit run ID produces predictable device IDs.
def test_derive_device_id__explicit_run_id_is_predictable() -> None:
    assert sim.derive_device_id("myrun", "cpu-high") == "sim-myrun-cpu-high"
    assert sim.derive_device_id("myrun", "all-anomalies") == "sim-myrun-all-anomalies"


def test_sanitize_run_id__strips_unsafe_characters() -> None:
    assert sim.sanitize_run_id("2026-07-26T16:05:00Z") == "2026-07-26T16-05-00Z"
    assert sim.sanitize_run_id("a/b c") == "a-b-c"
    with pytest.raises(sim.SimulatorError):
        sim.sanitize_run_id("///")


# 5. timestamps come from the fixed T0 and exact offsets.
def test_base_timestamp_and_offsets_are_deterministic() -> None:
    assert sim.BASE_TIMESTAMP == T0
    payload = sim.build_telemetry_payload(sim.BASE_TIMESTAMP + timedelta(seconds=30))
    assert payload["sampled_at"] == "2026-07-18T10:00:30Z"


# 6. every telemetry payload contains all required fields.
def test_build_telemetry_payload__contains_all_required_fields() -> None:
    payload = sim.build_telemetry_payload(T0)
    assert set(payload.keys()) == {
        "sampled_at",
        "cpu_utilization_pct",
        "memory_utilization_pct",
        "interface_error_rate",
        "interface_states",
        "bgp_sessions",
    }
    assert payload["cpu_utilization_pct"] == 50.0
    assert payload["memory_utilization_pct"] == 50.0
    assert payload["interface_error_rate"] == 0.0
    assert payload["interface_states"] == []
    assert payload["bgp_sessions"] == []


# 7. all-anomalies history steps use CPU 50.0.
# 8. final all-anomalies step expects CPU/link/BGP order.
def test_run_all_anomalies__history_steps_use_cpu_50_and_final_order_is_correct() -> (
    None
):
    device_id = "sim-test-all-anomalies"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(
                device_id, ["RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"]
            ),
            _incidents_response(
                [
                    _incident("i-cpu", device_id, "RULE-CPU-HIGH"),
                    _incident("i-flap", device_id, "RULE-LINK-FLAP"),
                    _incident("i-bgp", device_id, "RULE-BGP-DOWN"),
                ]
            ),
        ]
    )
    ctx = _make_ctx(client, device_id)

    sim.run_all_anomalies(ctx)

    telemetry_calls = [
        c for c in client.calls if c[0] == "POST" and "telemetry" in c[1]
    ]
    history_calls = telemetry_calls[:4]
    for _, _, body in history_calls:
        assert body is not None
        assert body["cpu_utilization_pct"] == 50.0
    bgp_established_call = telemetry_calls[4]
    final_call = telemetry_calls[5]
    assert bgp_established_call[2] is not None
    assert bgp_established_call[2]["cpu_utilization_pct"] == 95.0
    assert final_call[2] is not None
    assert final_call[2]["cpu_utilization_pct"] == 95.0
    assert final_call[2]["interface_states"] == [
        {"name": "GigabitEthernet0/1", "oper_state": "up"}
    ]
    assert final_call[2]["bgp_sessions"] == [
        {"neighbor_ip": "10.0.0.2", "state": "Idle"}
    ]


# 9. link-flap state sequence is exactly up/down/up/down/up.
def test_run_link_flap__state_sequence_is_exact() -> None:
    device_id = "sim-test-link-flap"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, ["RULE-LINK-FLAP"]),
            _incidents_response([_incident("i-flap", device_id, "RULE-LINK-FLAP")]),
        ]
    )
    ctx = _make_ctx(client, device_id)

    sim.run_link_flap(ctx)

    telemetry_calls = [
        c for c in client.calls if c[0] == "POST" and "telemetry" in c[1]
    ]
    states = [_body(c)["interface_states"][0]["oper_state"] for c in telemetry_calls]
    assert states == ["up", "down", "up", "down", "up"]


# 10. BGP sequence is Established then Idle.
def test_run_bgp_down__sequence_is_established_then_idle() -> None:
    device_id = "sim-test-bgp-down"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, ["RULE-BGP-DOWN"]),
            _incidents_response([_incident("i-bgp", device_id, "RULE-BGP-DOWN")]),
        ]
    )
    ctx = _make_ctx(client, device_id)

    sim.run_bgp_down(ctx)

    telemetry_calls = [
        c for c in client.calls if c[0] == "POST" and "telemetry" in c[1]
    ]
    bgp_states = [_body(c)["bgp_sessions"][0]["state"] for c in telemetry_calls]
    assert bgp_states == ["Established", "Idle"]


# 11. resolved-recurrence uses a new verification phase after resolution.
def test_run_resolved_recurrence__verifies_after_resolution() -> None:
    device_id = "sim-test-resolved-recurrence"
    resolved_incident = _incident("i-old", device_id, "RULE-CPU-HIGH")
    new_incident = _incident("i-new", device_id, "RULE-CPU-HIGH")
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, ["RULE-CPU-HIGH"]),
            _incidents_response([resolved_incident]),
            _resolve_response(resolved_incident),
            _telemetry_response(device_id, ["RULE-CPU-HIGH"]),
            # Deliberately reversed order — proves matching is by
            # incident_id, never by list position (test area 19).
            _incidents_response(
                [new_incident, {**resolved_incident, "status": "RESOLVED"}]
            ),
        ]
    )
    ctx = _make_ctx(client, device_id)

    sim.run_resolved_recurrence(ctx)

    resolve_calls = [c for c in client.calls if c[1].endswith("/resolve")]
    assert len(resolve_calls) == 1
    assert resolve_calls[0][0] == "POST"
    assert resolve_calls[0][1].endswith("/incidents/i-old/resolve")
    # The resolution call must happen strictly before the recurrence
    # telemetry submission and the final incidents re-fetch.
    resolve_index = client.calls.index(resolve_calls[0])
    later_calls = client.calls[resolve_index + 1 :]
    assert any(c[0] == "POST" and "telemetry" in c[1] for c in later_calls)
    assert any(c[0] == "GET" and c[1].endswith("/incidents") for c in later_calls)


# 12. normal scenario expects no anomaly and no incident.
def test_run_normal__passes_with_no_anomaly_and_no_incident() -> None:
    device_id = "sim-test-normal"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _incidents_response([]),
        ]
    )
    ctx = _make_ctx(client, device_id)

    sim.run_normal(ctx)  # must not raise


def test_run_normal__fails_if_an_incident_unexpectedly_exists() -> None:
    device_id = "sim-test-normal"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _incidents_response([_incident("i-1", device_id, "RULE-CPU-HIGH")]),
        ]
    )
    ctx = _make_ctx(client, device_id)

    with pytest.raises(sim.SimulatorError):
        sim.run_normal(ctx)


# 13. fake HTTP success path produces exit code 0.
def test_main__fake_success_path_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _ScriptedHttpClient(responses=[_health_response()])
    monkeypatch.setattr(sim, "JsonHttpClient", lambda timeout: client)

    original_derive = sim.derive_device_id

    def spying_derive(run_id: str, scenario: str) -> str:
        device_id = original_derive(run_id, scenario)
        client.responses.extend(
            [
                _config_response(device_id),
                _telemetry_response(device_id, []),
                _incidents_response([]),
            ]
        )
        return device_id

    monkeypatch.setattr(sim, "derive_device_id", spying_derive)

    exit_code = sim.main(["--scenario", "normal", "--base-url", "http://fake"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert (
        "SUMMARY: 1 passed, 0 failed, 4 HTTP requests issued, overall PASS"
        in captured.out
    )


# 14. HTTP 4xx/5xx produces exit code 1 and a useful message.
def test_main__http_error_exits_one_with_useful_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _ScriptedHttpClient(
        responses=[
            _health_response(),
            sim.HttpError(
                "POST",
                "http://fake/devices/x/config",
                500,
                {"code": "boom", "detail": "bad"},
            ),
        ]
    )
    monkeypatch.setattr(sim, "JsonHttpClient", lambda timeout: client)

    exit_code = sim.main(["--scenario", "normal", "--base-url", "http://fake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.out or "500" in captured.out


# 15. network failure produces exit code 1 without a traceback.
def test_main__network_error_exits_one_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _ScriptedHttpClient(
        responses=[
            _health_response(),
            sim.NetworkError(
                "POST", "http://fake/devices/x/config", OSError("connection refused")
            ),
        ]
    )
    monkeypatch.setattr(sim, "JsonHttpClient", lambda timeout: client)

    exit_code = sim.main(["--scenario", "normal", "--base-url", "http://fake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


# 16. malformed JSON produces exit code 1.
def test_main__malformed_response_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _ScriptedHttpClient(
        responses=[
            _health_response(),
            sim.MalformedResponseError(
                "POST", "http://fake/devices/x/config", b"not json"
            ),
        ]
    )
    monkeypatch.setattr(sim, "JsonHttpClient", lambda timeout: client)

    exit_code = sim.main(["--scenario", "normal", "--base-url", "http://fake"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out


# 17. health polling can succeed after transient failures.
def test_wait_for_health__succeeds_after_transient_failures() -> None:
    client = _ScriptedHttpClient(
        responses=[
            sim.NetworkError("GET", "http://fake/health", OSError("refused")),
            sim.NetworkError("GET", "http://fake/health", OSError("refused")),
            _health_response(),
        ]
    )
    clock = _FakeClock()

    sim.wait_for_health(
        client,
        "http://fake",
        deadline=100.0,
        poll_interval=1.0,
        now_fn=clock.now_fn,
        sleep_fn=clock.sleep_fn,
    )

    assert len(client.calls) == 3


# 18. health polling stops at the configured bound.
def test_wait_for_health__stops_at_configured_bound() -> None:
    client = _ScriptedHttpClient(
        responses=[
            sim.NetworkError("GET", "http://fake/health", OSError("refused"))
            for _ in range(10)
        ]
    )
    clock = _FakeClock()

    with pytest.raises(sim.SimulatorError):
        sim.wait_for_health(
            client,
            "http://fake",
            deadline=3.0,
            poll_interval=1.0,
            now_fn=clock.now_fn,
            sleep_fn=clock.sleep_fn,
        )

    assert clock.now >= 3.0
    assert len(client.calls) <= 4


# 19. incident verification does not depend on returned list order.
def test_find_incidents__does_not_depend_on_order() -> None:
    incidents = [
        _incident("i-3", "dev-other", "RULE-CPU-HIGH"),
        _incident("i-2", "dev-a", "RULE-BGP-DOWN"),
        _incident("i-1", "dev-a", "RULE-CPU-HIGH", status="RESOLVED"),
        _incident("i-4", "dev-a", "RULE-CPU-HIGH", status="OPEN"),
    ]

    matches = sim.find_incidents(
        incidents, device_id="dev-a", rule_ref="RULE-CPU-HIGH", status="OPEN"
    )

    assert [i["incident_id"] for i in matches] == ["i-4"]


# 20. request counter includes setup, verification, and resolution calls.
def test_execute_scenario__request_count_includes_every_call() -> None:
    device_id = "sim-test-cpu-high"
    client = _ScriptedHttpClient(
        responses=[
            _config_response(device_id),
            _telemetry_response(device_id, []),
            _telemetry_response(device_id, ["RULE-CPU-HIGH"]),
            _incidents_response([_incident("i-1", device_id, "RULE-CPU-HIGH")]),
        ]
    )
    logs: list[str] = []

    outcome = sim.execute_scenario(
        "cpu-high",
        base_url="http://fake",
        device_id=device_id,
        client=client,
        log=logs.append,
    )

    assert outcome.passed, outcome.message
    # 1 config + 2 telemetry + 1 incidents check = 4.
    assert outcome.request_count == 4


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls = 0

    def now_fn(self) -> float:
        return self.now

    def sleep_fn(self, seconds: float) -> None:
        self.sleep_calls += 1
        self.now += seconds
