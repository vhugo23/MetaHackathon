#!/usr/bin/env python3
"""Deterministic telemetry simulator (Day 11A).

Drives the real, public HTTP API — never the database, a repository, or a
UnitOfWork directly — to demonstrate the complete telemetry → anomaly →
incident → PostgreSQL → structured-log (AC-10) pipeline end to end.
Standard-library only, matching scripts/compose_smoke.py's and
scripts/browser_e2e.py's own stdlib-only discipline.

``GET /health`` proves API *liveness* only — it does not touch PostgreSQL
(README.md). The first ``POST /devices/{device_id}/config`` request is the
actual end-to-end, database-backed readiness check: a device row must be
durably created before any telemetry can be submitted for it, since there
is no dedicated device-registration endpoint.

Every simulator run derives fresh, scenario-specific device IDs from a run
ID (``sim-{run_id}-{scenario}``) — never a fixed ID — so repeated runs
never contaminate telemetry history, cause unexpected UPDATED outcomes, or
trigger confusing incident recurrence across unrelated invocations. Never
``spine-01``, which has a seeded exact-match policy (Day 3B/4B2).

AC-10's structured JSON stdout events are written by the API *process*,
not returned over HTTP — this simulator cannot and does not attempt to
observe them directly (they are invisible across a Docker Compose network
boundary). It prints a one-time reminder to view them via
``docker compose logs -f api`` and never invokes Docker itself.

Usage:
    python scripts/telemetry_simulator.py \\
        --base-url http://localhost:8080 \\
        --scenario suite
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

DEFAULT_BASE_URL = "http://localhost:8080"
BASE_TIMESTAMP = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
INTERFACE_NAME = "GigabitEthernet0/1"
BGP_NEIGHBOR = "10.0.0.2"

_UNSAFE_RUN_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class SimulatorError(RuntimeError):
    """Raised for any simulator-flow failure — scenario assertion, HTTP
    failure, or readiness timeout. Always caught before printing a
    result, never allowed to surface as a raw traceback."""


class HttpError(SimulatorError):
    """A non-2xx HTTP response (mirrors ``urllib.error.HTTPError``)."""

    def __init__(self, method: str, url: str, status: int, body: Any) -> None:
        code = None
        detail = None
        if isinstance(body, dict):
            code = body.get("code")
            detail = body.get("detail")
        message = f"{method} {url} -> HTTP {status}"
        if code is not None or detail is not None:
            message += f" (code={code!r}, detail={detail!r})"
        super().__init__(message)
        self.method = method
        self.url = url
        self.status = status
        self.body = body


class NetworkError(SimulatorError):
    """A transport-level failure (connection refused, timeout, DNS, ...)."""

    def __init__(self, method: str, url: str, cause: BaseException) -> None:
        super().__init__(f"{method} {url} -> network error: {cause}")
        self.method = method
        self.url = url
        self.cause = cause


class MalformedResponseError(SimulatorError):
    """A 2xx response whose body was not valid JSON."""

    def __init__(self, method: str, url: str, raw: bytes) -> None:
        snippet = raw[:200]
        super().__init__(f"{method} {url} -> malformed JSON response: {snippet!r}")
        self.method = method
        self.url = url
        self.raw = raw


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    body: Any
    method: str
    url: str


class HttpClientLike(Protocol):
    """Structural interface satisfied by both ``JsonHttpClient`` and any
    test double — never accessed via inheritance."""

    def request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> HttpResult: ...


def _parse_json_or_none(method: str, url: str, raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise MalformedResponseError(method, url, raw) from None


class JsonHttpClient:
    """Standard-library-only JSON HTTP client. Never retries a request —
    callers decide retry policy (only ``wait_for_health`` does)."""

    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    def request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> HttpResult:
        data = None
        headers = {"Accept": "application/json"}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                body = _parse_json_or_none(method, url, raw)
                return HttpResult(
                    status=response.status, body=body, method=method, url=url
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body = _parse_json_or_none(method, url, raw) if raw else None
            raise HttpError(method, url, exc.code, body) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise NetworkError(method, url, exc) from None


class CountingHttpClient:
    """Wraps any ``HttpClientLike``, counting every request issued through
    it — the basis for the simulator's total-request summary."""

    def __init__(self, inner: HttpClientLike) -> None:
        self._inner = inner
        self.count = 0

    def request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> HttpResult:
        self.count += 1
        return self._inner.request(method, url, json_body=json_body)


# --- Deterministic identifiers ------------------------------------------


def generate_run_id(now: datetime | None = None) -> str:
    """Uses the real clock only for this uniqueness token — every
    telemetry ``sampled_at`` value remains based on ``BASE_TIMESTAMP``."""
    moment = now if now is not None else datetime.now(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def sanitize_run_id(value: str) -> str:
    sanitized = _UNSAFE_RUN_ID_CHARS.sub("-", value).strip("-")
    if not sanitized:
        raise SimulatorError(f"run ID sanitizes to an empty string: {value!r}")
    return sanitized


def derive_device_id(run_id: str, scenario: str) -> str:
    return f"sim-{run_id}-{scenario}"


# --- Payload construction ------------------------------------------------


def format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def interface_state(name: str, oper_state: str) -> dict[str, str]:
    return {"name": name, "oper_state": oper_state}


def bgp_session(neighbor_ip: str, state: str) -> dict[str, str]:
    return {"neighbor_ip": neighbor_ip, "state": state}


def build_telemetry_payload(
    sampled_at: datetime,
    *,
    cpu_utilization_pct: float = 50.0,
    memory_utilization_pct: float = 50.0,
    interface_error_rate: float = 0.0,
    interface_states: tuple[dict[str, str], ...] = (),
    bgp_sessions: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "sampled_at": format_timestamp(sampled_at),
        "cpu_utilization_pct": cpu_utilization_pct,
        "memory_utilization_pct": memory_utilization_pct,
        "interface_error_rate": interface_error_rate,
        "interface_states": list(interface_states),
        "bgp_sessions": list(bgp_sessions),
    }


def build_minimal_cisco_config(hostname: str) -> str:
    return f"hostname {hostname}\n!\ninterface {INTERFACE_NAME}\n!\n"


# --- Scenario context and HTTP helpers -----------------------------------


@dataclass
class ScenarioContext:
    client: HttpClientLike
    base_url: str
    device_id: str
    log: Callable[[str], None]


def _url(ctx: ScenarioContext, path: str) -> str:
    return f"{ctx.base_url}{path}"


def _expect_dict(body: Any, description: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise SimulatorError(f"{description} was not a JSON object: {body!r}")
    return body


def _expect_list(body: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(body, list):
        raise SimulatorError(f"{description} was not a JSON array: {body!r}")
    return body


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SimulatorError(message)


def configure_device(ctx: ScenarioContext) -> dict[str, Any]:
    raw_config_text = build_minimal_cisco_config(ctx.device_id)
    result = ctx.client.request(
        "POST",
        _url(ctx, f"/devices/{ctx.device_id}/config"),
        json_body={"vendor": "cisco-ios-xe", "raw_config_text": raw_config_text},
    )
    ctx.log(f"POST /devices/{ctx.device_id}/config -> {result.status}")
    _assert(
        result.status == 201,
        f"device setup failed for {ctx.device_id!r}: expected 201, got {result.status} "
        "(an end-to-end readiness failure even though /health already succeeded)",
    )
    return _expect_dict(result.body, "configuration response")


def submit_telemetry(
    ctx: ScenarioContext, sampled_at: datetime, **overrides: Any
) -> dict[str, Any]:
    payload = build_telemetry_payload(sampled_at, **overrides)
    result = ctx.client.request(
        "POST", _url(ctx, f"/devices/{ctx.device_id}/telemetry"), json_body=payload
    )
    body = _expect_dict(result.body, "telemetry response")
    anomaly_ids = [anomaly.get("rule_id") for anomaly in body.get("anomalies", [])]
    ctx.log(
        f"POST /devices/{ctx.device_id}/telemetry sampled_at={payload['sampled_at']} "
        f"-> {result.status} anomalies={anomaly_ids}"
    )
    _assert(
        result.status == 201,
        f"telemetry submission failed: expected 201, got {result.status}",
    )
    return body


def get_incidents(ctx: ScenarioContext) -> list[dict[str, Any]]:
    result = ctx.client.request("GET", _url(ctx, "/incidents"))
    _assert(
        result.status == 200,
        f"GET /incidents failed: expected 200, got {result.status}",
    )
    return _expect_list(result.body, "incidents response")


def find_incidents(
    incidents: list[dict[str, Any]],
    *,
    device_id: str,
    rule_ref: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    matches = [
        incident for incident in incidents if incident.get("device_id") == device_id
    ]
    if rule_ref is not None:
        matches = [
            incident for incident in matches if incident.get("rule_ref") == rule_ref
        ]
    if status is not None:
        matches = [incident for incident in matches if incident.get("status") == status]
    return matches


def resolve_incident(ctx: ScenarioContext, incident_id: str) -> dict[str, Any]:
    result = ctx.client.request("POST", _url(ctx, f"/incidents/{incident_id}/resolve"))
    _assert(
        result.status == 200, f"resolve failed for {incident_id!r}: got {result.status}"
    )
    return _expect_dict(result.body, "resolve response")


def _log_incident_check(
    ctx: ScenarioContext, rule_ref: str, incident: dict[str, Any], passed: bool
) -> None:
    word = "PASS" if passed else "FAIL"
    ctx.log(
        f"[{word}] rule={rule_ref} incident_id={incident.get('incident_id')} "
        f"status={incident.get('status')} occurrence_count={incident.get('occurrence_count')}"
    )


def _require_one_open_incident(
    ctx: ScenarioContext, rule_ref: str, *, expected_occurrence_count: int
) -> dict[str, Any]:
    matches = find_incidents(
        get_incidents(ctx), device_id=ctx.device_id, rule_ref=rule_ref, status="OPEN"
    )
    if len(matches) != 1:
        for incident in matches:
            _log_incident_check(ctx, rule_ref, incident, False)
        raise SimulatorError(
            f"expected exactly one OPEN {rule_ref} incident for {ctx.device_id!r}, "
            f"found {len(matches)}"
        )
    incident = matches[0]
    passed = incident.get("occurrence_count") == expected_occurrence_count
    _log_incident_check(ctx, rule_ref, incident, passed)
    _assert(
        passed,
        f"{rule_ref} occurrence_count expected {expected_occurrence_count}, "
        f"got {incident.get('occurrence_count')}",
    )
    return incident


# --- Scenarios -------------------------------------------------------------


def run_normal(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    body = submit_telemetry(ctx, BASE_TIMESTAMP)
    _assert(
        set(body.keys()) == {"sample", "anomalies"},
        f"expected response keys exactly {{'sample', 'anomalies'}}, got {set(body.keys())}",
    )
    _assert(body["anomalies"] == [], f"expected no anomalies, got {body['anomalies']}")
    incidents = find_incidents(get_incidents(ctx), device_id=ctx.device_id)
    _assert(
        incidents == [],
        f"expected no incident for {ctx.device_id!r}, found {incidents}",
    )


def run_cpu_high(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    submit_telemetry(ctx, BASE_TIMESTAMP, cpu_utilization_pct=95.0)
    body = submit_telemetry(
        ctx, BASE_TIMESTAMP + timedelta(seconds=30), cpu_utilization_pct=95.0
    )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-CPU-HIGH"], f"expected only RULE-CPU-HIGH, got {rule_ids}"
    )
    _require_one_open_incident(ctx, "RULE-CPU-HIGH", expected_occurrence_count=1)


def run_cpu_update(ctx: ScenarioContext) -> None:
    """Self-contained — not a continuation of ``cpu-high``; uses its own
    device."""
    configure_device(ctx)
    submit_telemetry(ctx, BASE_TIMESTAMP, cpu_utilization_pct=95.0)
    submit_telemetry(
        ctx, BASE_TIMESTAMP + timedelta(seconds=30), cpu_utilization_pct=95.0
    )
    first = _require_one_open_incident(
        ctx, "RULE-CPU-HIGH", expected_occurrence_count=1
    )

    body = submit_telemetry(
        ctx, BASE_TIMESTAMP + timedelta(seconds=60), cpu_utilization_pct=95.0
    )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-CPU-HIGH"], f"expected only RULE-CPU-HIGH, got {rule_ids}"
    )

    second = _require_one_open_incident(
        ctx, "RULE-CPU-HIGH", expected_occurrence_count=2
    )
    _assert(
        second["incident_id"] == first["incident_id"], "incident_id changed on update"
    )
    _assert(second["created_at"] == first["created_at"], "created_at changed on update")
    _assert(
        second["last_seen_at"] != first["last_seen_at"], "last_seen_at did not advance"
    )


def run_link_flap(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    states = ["up", "down", "up", "down", "up"]
    body: dict[str, Any] = {}
    for offset, state in enumerate(states):
        body = submit_telemetry(
            ctx,
            BASE_TIMESTAMP + timedelta(seconds=10 * offset),
            cpu_utilization_pct=50.0,
            interface_states=(interface_state(INTERFACE_NAME, state),),
        )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-LINK-FLAP"], f"expected only RULE-LINK-FLAP, got {rule_ids}"
    )
    _require_one_open_incident(ctx, "RULE-LINK-FLAP", expected_occurrence_count=1)


def run_bgp_down(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    submit_telemetry(
        ctx,
        BASE_TIMESTAMP,
        cpu_utilization_pct=50.0,
        bgp_sessions=(bgp_session(BGP_NEIGHBOR, "Established"),),
    )
    body = submit_telemetry(
        ctx,
        BASE_TIMESTAMP + timedelta(seconds=30),
        cpu_utilization_pct=50.0,
        bgp_sessions=(bgp_session(BGP_NEIGHBOR, "Idle"),),
    )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-BGP-DOWN"], f"expected only RULE-BGP-DOWN, got {rule_ids}"
    )
    _require_one_open_incident(ctx, "RULE-BGP-DOWN", expected_occurrence_count=1)


def run_all_anomalies(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    # The four link-state history requests remain at CPU 50.0 — using 95.0
    # here would create the CPU incident prematurely, before the final
    # firing request (a pitfall discovered and fixed in Gates AC-10E/F).
    for offset, state in enumerate(["up", "down", "up", "down"]):
        submit_telemetry(
            ctx,
            BASE_TIMESTAMP + timedelta(seconds=10 * offset),
            cpu_utilization_pct=50.0,
            interface_states=(interface_state(INTERFACE_NAME, state),),
        )
    submit_telemetry(
        ctx,
        BASE_TIMESTAMP + timedelta(seconds=40),
        cpu_utilization_pct=95.0,
        bgp_sessions=(bgp_session(BGP_NEIGHBOR, "Established"),),
    )
    body = submit_telemetry(
        ctx,
        BASE_TIMESTAMP + timedelta(seconds=50),
        cpu_utilization_pct=95.0,
        interface_states=(interface_state(INTERFACE_NAME, "up"),),
        bgp_sessions=(bgp_session(BGP_NEIGHBOR, "Idle"),),
    )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"],
        f"expected exact CPU/link-flap/BGP order, got {rule_ids}",
    )

    incidents = get_incidents(ctx)
    seen_ids: set[str] = set()
    for rule_ref in ("RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"):
        matches = find_incidents(
            incidents, device_id=ctx.device_id, rule_ref=rule_ref, status="OPEN"
        )
        if len(matches) != 1:
            raise SimulatorError(
                f"expected exactly one OPEN {rule_ref} incident, found {len(matches)}"
            )
        incident = matches[0]
        passed = incident.get("occurrence_count") == 1
        _log_incident_check(ctx, rule_ref, incident, passed)
        _assert(passed, f"{rule_ref} occurrence_count != 1")
        seen_ids.add(incident["incident_id"])
    _assert(len(seen_ids) == 3, "the three incident IDs are not all distinct")


def run_resolved_recurrence(ctx: ScenarioContext) -> None:
    configure_device(ctx)
    submit_telemetry(ctx, BASE_TIMESTAMP, cpu_utilization_pct=95.0)
    submit_telemetry(
        ctx, BASE_TIMESTAMP + timedelta(seconds=30), cpu_utilization_pct=95.0
    )
    resolved_incident = _require_one_open_incident(
        ctx, "RULE-CPU-HIGH", expected_occurrence_count=1
    )

    resolved = resolve_incident(ctx, resolved_incident["incident_id"])
    _assert(resolved["status"] == "RESOLVED", "resolve did not return status RESOLVED")

    body = submit_telemetry(
        ctx, BASE_TIMESTAMP + timedelta(seconds=90), cpu_utilization_pct=95.0
    )
    rule_ids = [anomaly["rule_id"] for anomaly in body["anomalies"]]
    _assert(
        rule_ids == ["RULE-CPU-HIGH"], f"expected only RULE-CPU-HIGH, got {rule_ids}"
    )

    cpu_incidents = find_incidents(
        get_incidents(ctx), device_id=ctx.device_id, rule_ref="RULE-CPU-HIGH"
    )
    by_id = {incident["incident_id"]: incident for incident in cpu_incidents}
    _assert(
        len(cpu_incidents) == 2,
        f"expected exactly two CPU incidents (resolved + new), found {len(cpu_incidents)}",
    )
    _assert(
        resolved_incident["incident_id"] in by_id,
        "the originally resolved incident is missing from GET /incidents",
    )
    _assert(
        by_id[resolved_incident["incident_id"]]["status"] == "RESOLVED",
        "the original incident is no longer RESOLVED",
    )
    new_matches = [
        incident
        for incident in cpu_incidents
        if incident["incident_id"] != resolved_incident["incident_id"]
    ]
    _assert(len(new_matches) == 1, "expected exactly one new incident")
    new_incident = new_matches[0]
    _assert(new_incident["status"] == "OPEN", "the new incident is not OPEN")
    _assert(
        new_incident["fingerprint"]
        == by_id[resolved_incident["incident_id"]]["fingerprint"],
        "the new incident's fingerprint does not match the resolved incident's",
    )
    _assert(
        new_incident["occurrence_count"] == 1,
        "the new incident's occurrence_count != 1",
    )
    _log_incident_check(ctx, "RULE-CPU-HIGH", new_incident, True)


SCENARIO_RUNNERS: dict[str, Callable[[ScenarioContext], None]] = {
    "normal": run_normal,
    "cpu-high": run_cpu_high,
    "cpu-update": run_cpu_update,
    "link-flap": run_link_flap,
    "bgp-down": run_bgp_down,
    "all-anomalies": run_all_anomalies,
    "resolved-recurrence": run_resolved_recurrence,
}
SCENARIO_NAMES: list[str] = list(SCENARIO_RUNNERS.keys())


# --- Scenario execution and reporting --------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    name: str
    passed: bool
    message: str | None
    request_count: int


def execute_scenario(
    name: str,
    *,
    base_url: str,
    device_id: str,
    client: HttpClientLike,
    log: Callable[[str], None],
) -> ScenarioOutcome:
    counting_client = CountingHttpClient(client)
    ctx = ScenarioContext(
        client=counting_client, base_url=base_url, device_id=device_id, log=log
    )
    runner = SCENARIO_RUNNERS[name]
    try:
        runner(ctx)
    except SimulatorError as exc:
        log(f"[{name}] FAIL: {exc}")
        return ScenarioOutcome(
            name=name,
            passed=False,
            message=str(exc),
            request_count=counting_client.count,
        )
    log(f"[{name}] PASS")
    return ScenarioOutcome(
        name=name, passed=True, message=None, request_count=counting_client.count
    )


# --- Health wait -------------------------------------------------------------


def wait_for_health(
    client: HttpClientLike,
    base_url: str,
    *,
    deadline: float,
    poll_interval: float = 1.0,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda message: None,
) -> None:
    """Polls ``GET /health`` — API liveness only, never a claim about
    PostgreSQL health; the first configuration request is the actual
    database-backed readiness check (``configure_device``)."""
    last_error: str = "no attempt made"
    while now_fn() < deadline:
        try:
            result = client.request("GET", f"{base_url}/health")
            if result.status // 100 == 2 and isinstance(result.body, dict):
                return
            last_error = f"unexpected /health response: status={result.status} body={result.body!r}"
        except SimulatorError as exc:
            last_error = str(exc)
        log(f"waiting for API liveness... ({last_error})")
        sleep_fn(poll_interval)
    raise SimulatorError(
        f"API did not report healthy within --health-timeout: {last_error}"
    )


# --- CLI ---------------------------------------------------------------------


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value!r}")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic telemetry simulator for the Meta RNE Platform API."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--scenario", required=True, choices=[*SCENARIO_NAMES, "suite"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout", type=_positive_float, default=5.0)
    parser.add_argument("--health-timeout", type=_positive_float, default=30.0)
    return parser


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    base_url: str
    scenario: str
    run_id: str
    timeout: float
    health_timeout: float


def parse_config(argv: list[str]) -> SimulatorConfig:
    args = build_arg_parser().parse_args(argv)
    base_url = args.base_url.rstrip("/")
    run_id = sanitize_run_id(args.run_id) if args.run_id else generate_run_id()
    return SimulatorConfig(
        base_url=base_url,
        scenario=args.scenario,
        run_id=run_id,
        timeout=args.timeout,
        health_timeout=args.health_timeout,
    )


def _summary_line(passed: int, failed: int, total_requests: int) -> str:
    overall = "PASS" if failed == 0 else "FAIL"
    return f"SUMMARY: {passed} passed, {failed} failed, {total_requests} HTTP requests issued, overall {overall}"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = parse_config(argv)

    def log(message: str) -> None:
        print(message, flush=True)

    log(
        "AC-10 JSON events are written by the API process. In Docker Compose, "
        "view them with: docker compose logs -f api"
    )

    client = JsonHttpClient(timeout=config.timeout)
    total_requests = 0

    try:
        health_client = CountingHttpClient(client)
        wait_for_health(
            health_client,
            config.base_url,
            deadline=time.monotonic() + config.health_timeout,
            log=log,
        )
        total_requests += health_client.count
    except SimulatorError as exc:
        log(f"FAIL: {exc}")
        log(_summary_line(0, 1, total_requests))
        return 1

    names = SCENARIO_NAMES if config.scenario == "suite" else [config.scenario]
    outcomes: list[ScenarioOutcome] = []
    for name in names:
        device_id = derive_device_id(config.run_id, name)
        outcome = execute_scenario(
            name, base_url=config.base_url, device_id=device_id, client=client, log=log
        )
        outcomes.append(outcome)
        total_requests += outcome.request_count

    passed = sum(1 for outcome in outcomes if outcome.passed)
    failed = sum(1 for outcome in outcomes if not outcome.passed)
    log(_summary_line(passed, failed, total_requests))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
