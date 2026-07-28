#!/usr/bin/env python3
"""Windows-first, standard-library operator helper for the Meta RNE Platform
live demo (Day 11C2).

Starts the isolated Compose demo topology (docker-compose.yml's `db` + `api`
+ `frontend` services, Day 11C1), waits for readiness, seeds one
deterministic `all-anomalies` telemetry scenario through the existing
scripts/telemetry_simulator.py (never in-process, never against the browser),
and prints the exact browser URL and telemetry device ID a presenter needs.
Safely tears down only its own Compose project on request or on failure.

Standard-library only, in the same spirit as scripts/browser_e2e.py and
scripts/compose_smoke.py: a unique, project-scoped Compose run, `127.0.0.1`
used consistently for every browser-facing/API-facing URL (never internal
Compose DNS names, never `localhost`), and cleanup that only ever touches
the exact project it was given.

Usage:
    python scripts/demo.py start
    python scripts/demo.py start --project-name meta-rne-demo-ci --timeout-seconds 300
    python scripts/demo.py stop --project-name meta-rne-demo-ci
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIMULATOR_SCRIPT = REPO_ROOT / "scripts" / "telemetry_simulator.py"

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_HEALTH_BODY = {"status": "ok"}

DEFAULT_TIMEOUT_SECONDS = 300.0


class DemoError(RuntimeError):
    """Raised for any operator-facing failure — an invalid argument, a
    Compose/HTTP/simulator failure, or a readiness timeout. Always caught
    at the top level so cleanup runs before the process exits non-zero."""


# --------------------------------------------------------------------------
# Project name
# --------------------------------------------------------------------------


def validate_project_name(name: str) -> str:
    """Accepts only a conservative, Compose-safe lowercase form: letters,
    digits, hyphens, and underscores, beginning with a letter or digit."""
    if not _PROJECT_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid Compose project name: {name!r} — must be lowercase letters, "
            "digits, hyphens, and underscores, beginning with a letter or digit"
        )
    return name


def generate_project_name() -> str:
    suffix = uuid.uuid4().hex[:8]
    return validate_project_name(f"meta-rne-demo-{suffix}")


# --------------------------------------------------------------------------
# Run ID
# --------------------------------------------------------------------------


def validate_run_id(value: str) -> str:
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(
            f"invalid run ID: {value!r} — must be lowercase letters, digits, hyphens, "
            "and underscores, beginning with a letter or digit"
        )
    return value


def generate_run_id(now: datetime | None = None) -> str:
    moment = now if now is not None else datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    return validate_run_id(f"demo-{moment.strftime('%Y%m%dt%H%M%Sz')}-{suffix}")


def derive_device_id(run_id: str) -> str:
    """Mirrors scripts/telemetry_simulator.py's own
    ``derive_device_id(run_id, scenario)`` exactly for scenario
    ``all-anomalies`` — never recomputed independently."""
    return f"sim-{run_id}-all-anomalies"


# --------------------------------------------------------------------------
# Port validation and reservation
# --------------------------------------------------------------------------


def validate_explicit_port(value: int, *, label: str) -> int:
    if not (1 <= value <= 65535):
        raise ValueError(f"{label} port out of valid TCP range (1-65535): {value}")
    return value


def reject_duplicate_ports(*, db_port: int, api_port: int, frontend_port: int) -> None:
    ports = {"db": db_port, "api": api_port, "frontend": frontend_port}
    if len(set(ports.values())) != len(ports):
        raise ValueError(f"db/api/frontend ports must be distinct, got {ports}")


def check_port_available(port: int, *, label: str) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ValueError(f"{label} port {port} is already in use: {exc}") from exc
    finally:
        sock.close()


@dataclass
class PortReservation:
    sock: socket.socket
    port: int

    @property
    def is_released(self) -> bool:
        return self.sock.fileno() == -1

    def release(self) -> None:
        if not self.is_released:
            self.sock.close()


def reserve_ports(count: int) -> list[PortReservation]:
    """Holds ``count`` loopback sockets open simultaneously so the OS can
    never hand out the same free port twice for this run — released only
    immediately before Compose needs to bind them."""
    reservations: list[PortReservation] = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        reservations.append(PortReservation(sock=sock, port=sock.getsockname()[1]))
    ports = [r.port for r in reservations]
    if len(set(ports)) != len(ports):
        for reservation in reservations:
            reservation.release()
        raise DemoError(f"port reservation did not yield distinct ports: {ports}")
    return reservations


# --------------------------------------------------------------------------
# Environment construction
# --------------------------------------------------------------------------


def browser_url(frontend_port: int) -> str:
    return f"http://127.0.0.1:{frontend_port}"


def api_url(api_port: int) -> str:
    return f"http://127.0.0.1:{api_port}"


def build_runtime_environment(
    *, db_port: int, api_port: int, frontend_port: int
) -> dict[str, str]:
    """Exactly the five overrides docker-compose.yml's db/api/frontend
    services consume (Day 11C1) — never internal Compose DNS names in a
    browser-facing/API-facing value."""
    return {
        "META_RNE_DB_HOST_PORT": str(db_port),
        "META_RNE_API_HOST_PORT": str(api_port),
        "META_RNE_FRONTEND_HOST_PORT": str(frontend_port),
        "VITE_API_BASE_URL": api_url(api_port),
        "META_RNE_CORS_ALLOWED_ORIGINS": browser_url(frontend_port),
    }


def build_child_environment(
    base_env: dict[str, str], overrides: dict[str, str]
) -> dict[str, str]:
    """Returns a new dict — never mutates ``base_env`` (typically
    ``os.environ``) in place."""
    return {**base_env, **overrides}


# --------------------------------------------------------------------------
# Command assembly
# --------------------------------------------------------------------------


def compose_up_command(project_name: str, *, timeout_seconds: float) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "up",
        "--build",
        "--detach",
        "--wait",
        "--wait-timeout",
        str(int(timeout_seconds)),
    ]


def compose_down_command(project_name: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "down",
        "--volumes",
        "--remove-orphans",
    ]


def simulator_command(*, api_port: int, run_id: str) -> list[str]:
    return [
        sys.executable,
        str(SIMULATOR_SCRIPT),
        "--base-url",
        api_url(api_port),
        "--scenario",
        "all-anomalies",
        "--run-id",
        run_id,
    ]


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"[demo] {message}", flush=True)


def _log_err(message: str) -> None:
    print(f"[demo] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Subprocess execution
# --------------------------------------------------------------------------


def run_checked(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Runs ``command`` as an argument list (never ``shell=True``), with
    stdout/stderr inherited so the caller sees live progress — matches
    scripts/browser_e2e.py's and scripts/compose_smoke.py's own
    subprocess discipline."""
    _log(f"$ {' '.join(command)}  (cwd={cwd})")
    try:
        return subprocess.run(
            command, cwd=cwd, env=env, timeout=timeout, check=True, text=True
        )
    except subprocess.CalledProcessError as exc:
        raise DemoError(
            f"command failed (exit {exc.returncode}): {' '.join(command)}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DemoError(
            f"command timed out after {timeout}s: {' '.join(command)}"
        ) from exc


# --------------------------------------------------------------------------
# HTTP readiness — standard library only, fully injectable for tests
# --------------------------------------------------------------------------

HttpGetFn = Callable[[str, float], tuple[int, bytes]]


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait_for_api_ready(
    api_port: int,
    *,
    deadline_at: float,
    http_get: HttpGetFn = _http_get,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Proves API liveness by requiring the exact ``{"status": "ok"}``
    contract (mirrors scripts/browser_e2e.py's own ``wait_for_api_health``)
    — not merely a 200 with arbitrary content."""
    url = api_url(api_port) + "/health"
    last_error = "no attempt made"
    while now_fn() < deadline_at:
        try:
            status, raw = http_get(url, 5.0)
            if status == 200:
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                if body == _HEALTH_BODY:
                    return
                last_error = f"unexpected /health body: {raw!r}"
            else:
                last_error = f"unexpected /health status: {status}"
        except OSError as exc:
            last_error = str(exc)
        sleep_fn(2.0)
    raise DemoError(
        f"GET {url} never returned the expected liveness body: {last_error}"
    )


def wait_for_frontend_ready(
    frontend_port: int,
    *,
    deadline_at: float,
    http_get: HttpGetFn = _http_get,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Proves HTML was actually served — a 200 with an empty body does not
    satisfy readiness."""
    url = browser_url(frontend_port) + "/"
    last_error = "no attempt made"
    while now_fn() < deadline_at:
        try:
            status, raw = http_get(url, 5.0)
            if status == 200 and len(raw) > 0:
                return
            last_error = f"unexpected response: status={status} body_len={len(raw)}"
        except OSError as exc:
            last_error = str(exc)
        sleep_fn(1.0)
    raise DemoError(f"GET {url} never returned a non-empty HTML body: {last_error}")


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def clean_project(project_name: str, timeout_seconds: float) -> None:
    """Tears down only this run's own project — never anything belonging
    to another Compose project, never a prune command."""
    run_checked(compose_down_command(project_name), timeout=max(timeout_seconds, 60.0))


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def print_success_summary(
    *, project_name: str, api_port: int, frontend_port: int, device_id: str
) -> None:
    print("")
    print("=== Meta RNE Platform demo is ready ===")
    print(f"Compose project: {project_name}")
    print(f"Browser URL:     {browser_url(frontend_port)}")
    print(f"API URL:         {api_url(api_port)}")
    print(f"Telemetry device: {device_id}")
    print("Enter that device ID in the frontend's 'Telemetry device' field.")
    print("")
    print("When you are done, stop the demo with:")
    print(f"  python scripts/demo.py stop --project-name {project_name}")
    print("")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemoConfig:
    project_name: str
    db_port: int
    api_port: int
    frontend_port: int
    run_id: str
    timeout_seconds: float


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------


def start(config: DemoConfig) -> int:
    project_created = False
    try:
        env = build_child_environment(
            dict(os.environ),
            build_runtime_environment(
                db_port=config.db_port,
                api_port=config.api_port,
                frontend_port=config.frontend_port,
            ),
        )

        project_created = True
        run_checked(
            compose_up_command(
                config.project_name, timeout_seconds=config.timeout_seconds
            ),
            env=env,
            timeout=max(config.timeout_seconds, 300.0) + 60.0,
        )

        deadline = time.monotonic() + config.timeout_seconds
        wait_for_api_ready(config.api_port, deadline_at=deadline)

        deadline = time.monotonic() + config.timeout_seconds
        wait_for_frontend_ready(config.frontend_port, deadline_at=deadline)

        run_checked(
            simulator_command(api_port=config.api_port, run_id=config.run_id),
            timeout=max(config.timeout_seconds, 120.0),
        )

        device_id = derive_device_id(config.run_id)
        print_success_summary(
            project_name=config.project_name,
            api_port=config.api_port,
            frontend_port=config.frontend_port,
            device_id=device_id,
        )
        return 0
    except (DemoError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, DemoError) else "interrupted (Ctrl+C)"
        _log_err(f"START FAILURE: {message}")
        if project_created:
            try:
                clean_project(config.project_name, config.timeout_seconds)
            except DemoError as cleanup_exc:
                _log_err(f"cleanup also failed: {cleanup_exc}")
        return 1


# --------------------------------------------------------------------------
# Stop
# --------------------------------------------------------------------------


def stop(project_name: str) -> int:
    try:
        validate_project_name(project_name)
    except ValueError as exc:
        _log_err(f"STOP FAILURE: {exc}")
        return 2

    try:
        clean_project(project_name, DEFAULT_TIMEOUT_SECONDS)
    except DemoError as exc:
        _log_err(f"STOP FAILURE: {exc}")
        return 1

    print(f"Stopped and cleaned up Compose project: {project_name}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _port_type(value: str) -> int:
    return int(value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operator helper for the Meta RNE Platform live demo: starts the "
        "isolated Compose demo topology, waits for readiness, seeds one deterministic "
        "all-anomalies scenario, and prints the browser URL and telemetry device ID."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the isolated demo stack.")
    start_parser.add_argument(
        "--project-name",
        default=None,
        help="Compose project name. Defaults to a generated unique value.",
    )
    start_parser.add_argument(
        "--db-port",
        type=_port_type,
        default=None,
        help="Host port for PostgreSQL. Defaults to an available port.",
    )
    start_parser.add_argument(
        "--api-port",
        type=_port_type,
        default=None,
        help="Host port for the API. Defaults to an available port.",
    )
    start_parser.add_argument(
        "--frontend-port",
        type=_port_type,
        default=None,
        help="Host port for the frontend. Defaults to an available port.",
    )
    start_parser.add_argument(
        "--run-id",
        default=None,
        help="Telemetry simulator run ID. Defaults to a generated unique value.",
    )
    start_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Bound (seconds) for Compose startup/build and readiness waits. Default {DEFAULT_TIMEOUT_SECONDS:g}.",
    )

    stop_parser = subparsers.add_parser(
        "stop", help="Stop and clean up an isolated demo stack."
    )
    stop_parser.add_argument(
        "--project-name", required=True, help="Exact Compose project name to stop."
    )

    return parser


def _resolve_start_config(args: argparse.Namespace) -> DemoConfig:
    project_name = (
        validate_project_name(args.project_name)
        if args.project_name
        else generate_project_name()
    )
    run_id = validate_run_id(args.run_id) if args.run_id else generate_run_id()

    explicit_ports: dict[str, int] = {}
    if args.db_port is not None:
        explicit_ports["db"] = validate_explicit_port(args.db_port, label="db")
    if args.api_port is not None:
        explicit_ports["api"] = validate_explicit_port(args.api_port, label="api")
    if args.frontend_port is not None:
        explicit_ports["frontend"] = validate_explicit_port(
            args.frontend_port, label="frontend"
        )

    reservations: list[PortReservation] = []
    try:
        needed = [
            label for label in ("db", "api", "frontend") if label not in explicit_ports
        ]
        if needed:
            reservations = reserve_ports(len(needed))
            for label, reservation in zip(needed, reservations, strict=True):
                explicit_ports[label] = reservation.port

        reject_duplicate_ports(
            db_port=explicit_ports["db"],
            api_port=explicit_ports["api"],
            frontend_port=explicit_ports["frontend"],
        )

        # Explicit ports must be proven free (automatic ones already are,
        # by construction of reserve_ports) — checked while the automatic
        # reservations are still held, so nothing can steal a just-checked
        # explicit port before Compose starts either.
        if args.db_port is not None:
            check_port_available(explicit_ports["db"], label="db")
        if args.api_port is not None:
            check_port_available(explicit_ports["api"], label="api")
        if args.frontend_port is not None:
            check_port_available(explicit_ports["frontend"], label="frontend")
    finally:
        for reservation in reservations:
            reservation.release()

    return DemoConfig(
        project_name=project_name,
        db_port=explicit_ports["db"],
        api_port=explicit_ports["api"],
        frontend_port=explicit_ports["frontend"],
        run_id=run_id,
        timeout_seconds=args.timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_arg_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "start":
        try:
            config = _resolve_start_config(args)
        except ValueError as exc:
            _log_err(f"invalid arguments: {exc}")
            return 2
        return start(config)

    if args.command == "stop":
        return stop(args.project_name)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
