#!/usr/bin/env python3
"""Repeatable, isolated Docker Compose smoke validation (Day 6A).

Proves the first vertical slice works in its actual deployed Compose
shape — real image build, real PostgreSQL, real Alembic migration before
Uvicorn starts, real idempotent Slice 1 policy seeding, real HTTP
ingestion/query traffic, and real state persistence across an API process
restart — not only through direct pytest fixtures (backend/tests/).

Standard-library only, runs identically on a developer's machine and in
CI. ``--project-name`` is the single authoritative Compose project
selector on every invocation (never ``COMPOSE_PROJECT_NAME``), so this
script can never collide with, or tear down, anything outside the project
it was given — in particular the native PostgreSQL service on host 5432
and the disposable ``meta-rne-test-db`` container on host 5433 are never
touched, regardless of what this script does.

Usage:
    python scripts/compose_smoke.py \\
        --project-name meta-rne-smoke \\
        --api-port 58080 \\
        --db-port 55432

Note on the device ID: the seeded Slice 1 policy
(``meta_rne.persistence.seeds.build_slice1_policies``) applies only to
``device_id == "spine-01"`` — exact-match only, no wildcard (a documented
Day 3B/4B2 binding decision, not a defect). This script therefore submits
configuration for device ``spine-01``, not a smoke-specific ID: isolation
is already guaranteed by every row living inside this run's own disposable
Postgres instance, under its own Compose project name, discarded at
teardown — no real ``spine-01`` data ever coexists with it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEVICE_ID = "spine-01"

# Adapter-supported Cisco IOS-XE config: hostname + one interface, no ACL
# assigned inbound on GigabitEthernet0/1 — the exact missing-required-ACL
# shape the seeded ``policy-acl-external-in`` policy is written to catch
# (mirrors backend/tests/fixtures/configs/cisco/cisco_missing_required_acl.txt).
MISSING_ACL_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " description Uplink to core-01\n"
    " ip address 10.0.0.1 255.255.255.252\n"
    " no shutdown\n"
    "!\n"
    "router bgp 65001\n"
    " neighbor 10.0.0.2 remote-as 65002\n"
    "!\n"
    "end\n"
)

EXPECTED_ACL_NAME = "ACL-EXTERNAL-IN"
EXPECTED_INTERFACE_NAME = "GigabitEthernet0/1"
EXPECTED_DIRECTION = "in"


class SmokeError(RuntimeError):
    """Raised for any smoke-flow failure — always caught at the top level
    so cleanup/diagnostics run before the process exits non-zero."""


@dataclass
class SmokeConfig:
    project_name: str
    api_port: int
    db_port: int
    timeout: float
    keep: bool


@dataclass
class HttpResult:
    status: int
    body: Any
    raw_command: str = field(default="")


def parse_args(argv: list[str]) -> SmokeConfig:
    parser = argparse.ArgumentParser(
        description="Docker Compose smoke validation for the Meta RNE Platform backend."
    )
    parser.add_argument("--project-name", default="meta-rne-smoke")
    parser.add_argument("--api-port", type=int, default=58080)
    parser.add_argument("--db-port", type=int, default=55432)
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Bound (seconds) for health-wait polling and each subprocess/HTTP call.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Skip teardown (containers/volumes/network left running) for post-mortem inspection.",
    )
    args = parser.parse_args(argv)
    return SmokeConfig(
        project_name=args.project_name,
        api_port=args.api_port,
        db_port=args.db_port,
        timeout=args.timeout,
        keep=args.keep,
    )


def _log(message: str) -> None:
    print(f"[compose-smoke] {message}", flush=True)


def compose_command(project_name: str, *args: str) -> list[str]:
    return ["docker", "compose", "--project-name", project_name, *args]


def run_compose(
    config: SmokeConfig, *args: str, timeout: float | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    cmd = compose_command(config.project_name, *args)
    _log(f"$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout if timeout is not None else config.timeout,
    )
    if check and result.returncode != 0:
        raise SmokeError(
            f"command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result


def http_request(
    method: str, url: str, *, timeout: float, json_body: dict[str, Any] | None = None
) -> HttpResult:
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            body = json.loads(raw) if raw else None
            return HttpResult(status=response.status, body=body, raw_command=f"{method} {url}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        return HttpResult(status=exc.code, body=body, raw_command=f"{method} {url}")


def assert_http(condition: bool, message: str, result: HttpResult) -> None:
    if not condition:
        raise SmokeError(
            f"{message}\ncommand: {result.raw_command}\n"
            f"status: {result.status}\nbody: {result.body!r}"
        )


def clean_project(config: SmokeConfig) -> None:
    """Removes only this run's own project — containers, volumes, and any
    orphaned services — never anything belonging to another Compose
    project or an unrelated container like meta-rne-test-db."""
    run_compose(
        config,
        "down",
        "--volumes",
        "--remove-orphans",
        timeout=config.timeout,
        check=False,
    )


def wait_for_healthy(config: SmokeConfig, service: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        result = run_compose(config, "ps", "-q", service, timeout=config.timeout, check=False)
        container_id = result.stdout.strip()
        if container_id:
            inspect = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.timeout,
            )
            status = inspect.stdout.strip()
            _log(f"{service} health: {status or 'starting'}")
            if status == "healthy":
                return
        time.sleep(2)
    raise SmokeError(f"{service} did not become healthy within {config.timeout}s")


def wait_for_health_endpoint(config: SmokeConfig, deadline: float) -> None:
    base_url = f"http://127.0.0.1:{config.api_port}"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = http_request("GET", f"{base_url}/health", timeout=5)
            if result.status == 200 and result.body == {"status": "ok"}:
                return
            last_error = SmokeError(f"unexpected /health response: {result.body!r}")
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(2)
    raise SmokeError(f"GET /health never returned the expected body: {last_error}")


def verify_migrations_ran_before_uvicorn(config: SmokeConfig) -> None:
    logs = run_compose(config, "logs", "--no-color", "api", timeout=config.timeout)
    if "Context impl PostgresqlImpl" not in logs.stdout:
        raise SmokeError(
            "expected Alembic's 'Context impl PostgresqlImpl' line in api logs "
            "(proof migrations ran before Uvicorn started) — not found"
        )
    psql = run_compose(
        config,
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "meta_rne",
        "-d",
        "meta_rne",
        "-c",
        "\\dt alembic_version",
        timeout=config.timeout,
    )
    if "alembic_version" not in psql.stdout:
        raise SmokeError("alembic_version table not found in the db container")


def submit_configuration(config: SmokeConfig) -> HttpResult:
    base_url = f"http://127.0.0.1:{config.api_port}"
    return http_request(
        "POST",
        f"{base_url}/devices/{DEVICE_ID}/config",
        timeout=config.timeout,
        json_body={"vendor": "cisco-ios-xe", "raw_config_text": MISSING_ACL_CONFIG},
    )


def get_incidents(config: SmokeConfig) -> HttpResult:
    base_url = f"http://127.0.0.1:{config.api_port}"
    return http_request("GET", f"{base_url}/incidents", timeout=config.timeout)


def find_device_incident(incidents_result: HttpResult) -> dict[str, Any]:
    assert_http(
        isinstance(incidents_result.body, list),
        "GET /incidents did not return a direct JSON array",
        incidents_result,
    )
    matching = [
        incident for incident in incidents_result.body if incident.get("device_id") == DEVICE_ID
    ]
    assert_http(
        len(matching) == 1,
        f"expected exactly one incident for device_id={DEVICE_ID!r}, found {len(matching)}",
        incidents_result,
    )
    return matching[0]


def assert_evidence(
    incident: dict[str, Any], expected_snapshot_id: str, result: HttpResult
) -> None:
    evidence = incident["evidence"]
    actual_snapshot_id = evidence["source_snapshot_id"]
    assert_http(
        actual_snapshot_id == expected_snapshot_id,
        f"evidence.source_snapshot_id {actual_snapshot_id!r} != {expected_snapshot_id!r}",
        result,
    )
    assert_http(
        evidence["expected_acl_name"] == EXPECTED_ACL_NAME,
        f"evidence.expected_acl_name {evidence['expected_acl_name']!r} != {EXPECTED_ACL_NAME!r}",
        result,
    )
    assert_http(
        evidence["interface_name"] == EXPECTED_INTERFACE_NAME,
        f"evidence.interface_name {evidence['interface_name']!r} != {EXPECTED_INTERFACE_NAME!r}",
        result,
    )
    assert_http(
        evidence["direction"] == EXPECTED_DIRECTION,
        f"evidence.direction {evidence['direction']!r} != {EXPECTED_DIRECTION!r}",
        result,
    )


def run_smoke_flow(config: SmokeConfig) -> None:
    # Each waiting stage gets its own fresh deadline, computed immediately
    # before that stage starts — a single deadline shared across the build
    # step and every subsequent wait would silently eat most of its own
    # budget during the (multi-minute) image build, starving the health
    # waits that come after it.

    _log("Step 1/9: docker compose config --quiet")
    run_compose(config, "config", "--quiet")

    _log("Step 2/9: pre-clean any leftover project state")
    clean_project(config)

    _log("Step 3/9: build and start (db + api)")
    run_compose(config, "up", "--build", "-d", timeout=max(config.timeout, 300))

    _log("Step 4/9: wait for db and api to report healthy")
    wait_for_healthy(config, "db", time.monotonic() + config.timeout)
    wait_for_healthy(config, "api", time.monotonic() + config.timeout)
    wait_for_health_endpoint(config, time.monotonic() + config.timeout)

    _log("Step 5/9: confirm Alembic ran before Uvicorn started")
    verify_migrations_ran_before_uvicorn(config)

    _log("Step 6/9: first ingestion (POST /devices/spine-01/config)")
    first_response = submit_configuration(config)
    assert_http(first_response.status == 201, "first POST did not return 201", first_response)
    body = first_response.body
    assert_http(body["device_id"] == DEVICE_ID, "device_id mismatch", first_response)
    assert_http(
        isinstance(body["snapshot_id"], str) and body["snapshot_id"],
        "snapshot_id is not a non-empty string",
        first_response,
    )
    assert_http(body["violations_detected"] == 1, "violations_detected != 1", first_response)
    assert_http(body["incidents_created"] == 1, "incidents_created != 1", first_response)
    assert_http(body["incidents_updated"] == 0, "incidents_updated != 0", first_response)
    assert_http("normalized_config" in body, "normalized_config missing", first_response)
    interface_names = {interface["name"] for interface in body["normalized_config"]["interfaces"]}
    assert_http(
        EXPECTED_INTERFACE_NAME in interface_names,
        f"normalized_config.interfaces does not contain {EXPECTED_INTERFACE_NAME!r}",
        first_response,
    )
    first_snapshot_id = body["snapshot_id"]

    _log("Step 6/9: GET /incidents after first ingestion")
    incidents_result = get_incidents(config)
    incident = find_device_incident(incidents_result)
    assert_http(incident["occurrence_count"] == 1, "occurrence_count != 1", incidents_result)
    assert_http(incident["status"] == "OPEN", "status != OPEN", incidents_result)
    assert_http(
        incident["source"] == "POLICY_VIOLATION", "source != POLICY_VIOLATION", incidents_result
    )
    assert_http(incident["fingerprint"], "fingerprint is empty", incidents_result)
    assert_evidence(incident, first_snapshot_id, incidents_result)
    first_incident_id = incident["incident_id"]
    first_last_seen_at = incident["last_seen_at"]

    _log("Step 7/9: second ingestion before restart (same config, again)")
    second_response = submit_configuration(config)
    assert_http(second_response.status == 201, "second POST did not return 201", second_response)
    second_body = second_response.body
    second_snapshot_id = second_body["snapshot_id"]
    assert_http(
        second_snapshot_id != first_snapshot_id,
        "second snapshot_id did not differ from the first",
        second_response,
    )
    assert_http(
        second_body["violations_detected"] == 1, "violations_detected != 1", second_response
    )
    assert_http(second_body["incidents_created"] == 0, "incidents_created != 0", second_response)
    assert_http(second_body["incidents_updated"] == 1, "incidents_updated != 1", second_response)

    incidents_result = get_incidents(config)
    incident = find_device_incident(incidents_result)
    assert_http(
        incident["incident_id"] == first_incident_id, "incident_id changed", incidents_result
    )
    assert_http(incident["occurrence_count"] == 2, "occurrence_count != 2", incidents_result)
    assert_http(
        incident["last_seen_at"] >= first_last_seen_at,
        "last_seen_at went backwards",
        incidents_result,
    )
    assert_evidence(incident, second_snapshot_id, incidents_result)
    second_last_seen_at = incident["last_seen_at"]

    _log("Step 8/9: restart api and verify persistence (no db reset)")
    run_compose(config, "restart", "api", timeout=max(config.timeout, 60))
    restart_deadline = time.monotonic() + config.timeout
    wait_for_healthy(config, "api", restart_deadline)
    wait_for_health_endpoint(config, restart_deadline)

    incidents_result = get_incidents(config)
    incident = find_device_incident(incidents_result)
    assert_http(
        incident["incident_id"] == first_incident_id,
        "incident_id changed across restart",
        incidents_result,
    )
    assert_http(
        incident["occurrence_count"] == 2,
        "occurrence_count changed across restart (expected still 2)",
        incidents_result,
    )

    _log("Step 9/9: third ingestion after restart")
    third_response = submit_configuration(config)
    assert_http(third_response.status == 201, "third POST did not return 201", third_response)
    third_body = third_response.body
    third_snapshot_id = third_body["snapshot_id"]
    assert_http(third_body["incidents_created"] == 0, "incidents_created != 0", third_response)
    assert_http(third_body["incidents_updated"] == 1, "incidents_updated != 1", third_response)
    assert_http(
        third_snapshot_id not in (first_snapshot_id, second_snapshot_id),
        "third snapshot_id is not distinct from the first two",
        third_response,
    )

    incidents_result = get_incidents(config)
    incident = find_device_incident(incidents_result)
    assert_http(
        incident["incident_id"] == first_incident_id,
        "incident_id changed after third ingestion",
        incidents_result,
    )
    assert_http(incident["occurrence_count"] == 3, "occurrence_count != 3", incidents_result)
    assert_http(
        incident["last_seen_at"] >= second_last_seen_at,
        "last_seen_at went backwards after third ingestion",
        incidents_result,
    )
    assert_evidence(incident, third_snapshot_id, incidents_result)

    _log("Smoke flow complete — all assertions passed.")


def print_failure_diagnostics(config: SmokeConfig) -> None:
    _log("=== FAILURE DIAGNOSTICS ===")
    for args in (("ps",), ("logs", "--no-color", "db"), ("logs", "--no-color", "api")):
        try:
            result = run_compose(config, *args, timeout=30, check=False)
            _log(f"--- docker compose --project-name {config.project_name} {' '.join(args)} ---")
            print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        except (subprocess.TimeoutExpired, SmokeError) as exc:
            _log(f"failed to collect diagnostics for {args}: {exc}")


def main(argv: list[str]) -> int:
    # Some Windows terminals default stdout to the console codepage
    # (e.g. cp1252), which cannot encode this script's own em-dashes —
    # force UTF-8 so log output never raises/garbles on any platform.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = parse_args(argv)

    os.environ["META_RNE_API_HOST_PORT"] = str(config.api_port)
    os.environ["META_RNE_DB_HOST_PORT"] = str(config.db_port)

    try:
        run_smoke_flow(config)
    except SmokeError as exc:
        _log(f"SMOKE FAILURE: {exc}")
        print_failure_diagnostics(config)
        return 1
    except subprocess.TimeoutExpired as exc:
        _log(f"SMOKE FAILURE: command timed out: {exc}")
        print_failure_diagnostics(config)
        return 1
    finally:
        if config.keep:
            _log(f"--keep set: leaving project {config.project_name!r} running")
        else:
            _log("cleaning up project (finally block, runs on success or failure)")
            clean_project(config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
