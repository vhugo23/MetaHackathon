#!/usr/bin/env python3
"""Isolated, cross-platform browser (Playwright/Chromium) E2E orchestration
for the Meta RNE Platform (Day 6D).

Proves the configuration-submission -> incident-refresh vertical slice
through the real, deployed shape: a real Chromium browser, driven by
Playwright, against a real production Vite build served via `vite preview`,
talking cross-origin over real HTTP to a real FastAPI process, backed by a
real, disposable PostgreSQL instance started via the repository's existing
`docker-compose.yml` (`db` + `api` services only) — never in-memory
repositories, never a mocked/fulfilled API response.

Standard-library only, in the same spirit as scripts/compose_smoke.py: a
unique, project-scoped Compose run, `127.0.0.1` used consistently, and
unconditional cleanup (`docker compose down --volumes --remove-orphans`)
regardless of outcome.

Usage:
    python scripts/browser_e2e.py
    python scripts/browser_e2e.py --project-name meta-rne-browser-e2e-ci --timeout 180
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_HEALTH_BODY = {"status": "ok"}


class BrowserE2EError(RuntimeError):
    """Raised for any orchestration-flow failure — always caught at the top
    level so cleanup/diagnostics run before the process exits non-zero."""


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
    return validate_project_name(f"meta-rne-browser-e2e-{suffix}")


# --------------------------------------------------------------------------
# Port reservation — all three sockets held open simultaneously so the OS
# can never hand out the same free port twice for this run.
# --------------------------------------------------------------------------


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
    reservations: list[PortReservation] = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        reservations.append(PortReservation(sock=sock, port=sock.getsockname()[1]))
    ports = [r.port for r in reservations]
    if len(set(ports)) != len(ports):
        for reservation in reservations:
            reservation.release()
        raise BrowserE2EError(f"port reservation did not yield distinct ports: {ports}")
    return reservations


# --------------------------------------------------------------------------
# Runtime environment
# --------------------------------------------------------------------------


def build_runtime_environment(db_port: int, api_port: int, frontend_port: int) -> dict[str, str]:
    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    api_origin = f"http://127.0.0.1:{api_port}"
    return {
        "META_RNE_DB_HOST_PORT": str(db_port),
        "META_RNE_API_HOST_PORT": str(api_port),
        "META_RNE_CORS_ALLOWED_ORIGINS": frontend_origin,
        "VITE_API_BASE_URL": api_origin,
        "PLAYWRIGHT_BASE_URL": frontend_origin,
    }


# --------------------------------------------------------------------------
# Command assembly
# --------------------------------------------------------------------------


def compose_command(project_name: str, *args: str) -> list[str]:
    return ["docker", "compose", "--project-name", project_name, *args]


def docker_ps_filter_command(project_name: str) -> list[str]:
    return ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project_name}", "--quiet"]


def docker_volume_filter_command(project_name: str) -> list[str]:
    return [
        "docker",
        "volume",
        "ls",
        "--filter",
        f"label=com.docker.compose.project={project_name}",
        "--quiet",
    ]


def vite_preview_command(frontend_dir: Path, port: int) -> list[str]:
    vite_entry = frontend_dir / "node_modules" / "vite" / "bin" / "vite.js"
    return [
        "node",
        str(vite_entry),
        "preview",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--strictPort",
    ]


def resolve_npm_executable(platform_system: str) -> str:
    return "npm.cmd" if platform_system == "Windows" else "npm"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


@dataclass
class BrowserE2EConfig:
    project_name: str
    timeout: float


def parse_args(argv: list[str]) -> BrowserE2EConfig:
    parser = argparse.ArgumentParser(
        description="Isolated cross-platform browser (Playwright/Chromium) E2E run "
        "for the Meta RNE Platform's configuration-submission -> incident-refresh slice."
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help="Deterministic Compose project name override (e.g. for CI). "
        "Must be lowercase letters/digits/hyphens/underscores, starting with a letter or digit. "
        "Defaults to a freshly generated unique name.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Bound (seconds) for readiness polling and most subprocess calls.",
    )
    args = parser.parse_args(argv)
    project_name = (
        validate_project_name(args.project_name) if args.project_name else generate_project_name()
    )
    return BrowserE2EConfig(project_name=project_name, timeout=args.timeout)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"[browser-e2e] {message}", flush=True)


# --------------------------------------------------------------------------
# Executable validation
# --------------------------------------------------------------------------


def check_required_executables(npm_executable: str) -> None:
    missing = [name for name in ("docker", "node", npm_executable) if shutil.which(name) is None]
    if missing:
        raise BrowserE2EError(
            f"required executable(s) not found on PATH: {', '.join(missing)}"
        )


# --------------------------------------------------------------------------
# Subprocess helpers
# --------------------------------------------------------------------------


def run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    _log(f"$ {' '.join(command)}  (cwd={cwd})")
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise BrowserE2EError(
            f"command failed (exit {result.returncode}): {' '.join(command)}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def wait_for_container_healthy(project_name: str, service: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        result = subprocess.run(
            compose_command(project_name, "ps", "-q", service),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        container_id = result.stdout.strip()
        if container_id:
            inspect = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            status = inspect.stdout.strip()
            _log(f"{service} health: {status or 'starting'}")
            if status == "healthy":
                return
        time.sleep(2)
    raise BrowserE2EError(f"{service} did not become healthy before the deadline")


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait_for_api_health(api_port: int, deadline: float) -> None:
    url = f"http://127.0.0.1:{api_port}/health"
    last_error: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            status, raw = _http_get(url, timeout=5)
            if status == 200:
                try:
                    body: Any = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                if body == _HEALTH_BODY:
                    return
                last_error = f"unexpected /health body: {raw!r}"
            else:
                last_error = f"unexpected /health status: {status}"
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise BrowserE2EError(f"GET {url} never returned the expected body: {last_error}")


def wait_for_frontend_ready(
    frontend_port: int, deadline: float, preview_process: subprocess.Popen[bytes]
) -> None:
    url = f"http://127.0.0.1:{frontend_port}/"
    last_error: str = "no attempt made"
    while time.monotonic() < deadline:
        exit_code = preview_process.poll()
        if exit_code is not None:
            raise BrowserE2EError(
                f"vite preview process exited before the frontend became ready "
                f"(return code {exit_code})"
            )
        try:
            status, _ = _http_get(url, timeout=5)
            if status == 200:
                return
            last_error = f"unexpected status: {status}"
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise BrowserE2EError(f"GET {url} never returned HTTP 200: {last_error}")


# --------------------------------------------------------------------------
# Vite preview process lifecycle
# --------------------------------------------------------------------------


def start_vite_preview(frontend_dir: Path, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    command = vite_preview_command(frontend_dir, port)
    _log(f"$ {' '.join(command)}  (cwd={frontend_dir})")
    # stdout/stderr are inherited (not captured) so a preview startup
    # failure remains visible in local and CI logs.
    return subprocess.Popen(command, cwd=frontend_dir, env=env)


def stop_process(proc: subprocess.Popen[bytes] | None, wait_seconds: float = 10.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=wait_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        _log("warning: vite preview process did not exit after kill()")


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------


def print_failure_diagnostics(project_name: str) -> None:
    _log("=== FAILURE DIAGNOSTICS (db, api) ===")
    try:
        result = subprocess.run(
            compose_command(project_name, "logs", "--no-color", "db", "api"),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(f"failed to collect db/api diagnostics: {exc}")


def clean_project(project_name: str, timeout: float) -> None:
    subprocess.run(
        compose_command(project_name, "down", "--volumes", "--remove-orphans"),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def verify_cleanup(project_name: str) -> bool:
    """Confirms no container or volume carrying this run's Compose project
    label remains. Never touches another project's resources."""
    ok = True
    containers = subprocess.run(
        docker_ps_filter_command(project_name),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    remaining_containers = [line for line in containers.stdout.splitlines() if line.strip()]
    if remaining_containers:
        _log(f"cleanup verification failed: containers remain: {remaining_containers}")
        ok = False

    volumes = subprocess.run(
        docker_volume_filter_command(project_name),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    remaining_volumes = [line for line in volumes.stdout.splitlines() if line.strip()]
    if remaining_volumes:
        _log(f"cleanup verification failed: volumes remain: {remaining_volumes}")
        ok = False

    return ok


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------


def run_browser_e2e(config: BrowserE2EConfig) -> int:
    npm_executable = resolve_npm_executable(platform.system())
    check_required_executables(npm_executable)

    reservations = reserve_ports(3)
    db_reservation, api_reservation, frontend_reservation = reservations

    runtime_env = build_runtime_environment(
        db_port=db_reservation.port,
        api_port=api_reservation.port,
        frontend_port=frontend_reservation.port,
    )

    _log(f"project name: {config.project_name}")
    _log(f"reserved ports — db={db_reservation.port} api={api_reservation.port} frontend={frontend_reservation.port}")

    preview_process: subprocess.Popen[bytes] | None = None
    exit_code = 1
    orchestration_error: BrowserE2EError | None = None

    try:
        try:
            # Release db/api reservations immediately before Compose start —
            # docker-compose.yml (not this script) binds those host ports.
            db_reservation.release()
            api_reservation.release()

            compose_env = {
                **os.environ,
                "META_RNE_DB_HOST_PORT": runtime_env["META_RNE_DB_HOST_PORT"],
                "META_RNE_API_HOST_PORT": runtime_env["META_RNE_API_HOST_PORT"],
                "META_RNE_CORS_ALLOWED_ORIGINS": runtime_env["META_RNE_CORS_ALLOWED_ORIGINS"],
            }

            _log("starting db + api via docker compose")
            run_subprocess(
                compose_command(config.project_name, "up", "--build", "-d", "db", "api"),
                cwd=REPO_ROOT,
                env=compose_env,
                timeout=max(config.timeout, 300),
            )

            _log("waiting for db + api container health")
            wait_for_container_healthy(config.project_name, "db", time.monotonic() + config.timeout)
            wait_for_container_healthy(config.project_name, "api", time.monotonic() + config.timeout)

            _log("waiting for GET /health")
            wait_for_api_health(api_reservation.port, time.monotonic() + config.timeout)

            _log("building the frontend with VITE_API_BASE_URL baked in")
            build_env = {**os.environ, "VITE_API_BASE_URL": runtime_env["VITE_API_BASE_URL"]}
            run_subprocess(
                [npm_executable, "run", "build"],
                cwd=FRONTEND_DIR,
                env=build_env,
                timeout=max(config.timeout, 180),
            )

            # Release the frontend reservation immediately before preview start.
            frontend_reservation.release()

            preview_env = {**os.environ}
            preview_process = start_vite_preview(FRONTEND_DIR, frontend_reservation.port, preview_env)

            _log("waiting for the frontend preview to respond")
            wait_for_frontend_ready(
                frontend_reservation.port, time.monotonic() + config.timeout, preview_process
            )

            _log("running the Playwright browser test against the real stack")
            playwright_env = {**os.environ, "PLAYWRIGHT_BASE_URL": runtime_env["PLAYWRIGHT_BASE_URL"]}
            playwright_result = subprocess.run(
                [npm_executable, "run", "test:e2e:direct"],
                cwd=FRONTEND_DIR,
                env=playwright_env,
            )
            exit_code = playwright_result.returncode
        except BrowserE2EError as exc:
            orchestration_error = exc
            _log(f"ORCHESTRATION FAILURE: {exc}")
            print_failure_diagnostics(config.project_name)
            exit_code = 1
    finally:
        stop_process(preview_process)
        for reservation in reservations:
            reservation.release()
        _log("cleaning up project (finally block, runs on success or failure)")
        cleanup_ok = False
        try:
            clean_project(config.project_name, config.timeout)
            cleanup_ok = verify_cleanup(config.project_name)
        except (subprocess.TimeoutExpired, OSError) as exc:
            _log(f"cleanup itself failed: {exc}")

    if orchestration_error is not None:
        _log(f"orchestration did not complete: {orchestration_error}")
    if not cleanup_ok:
        _log("cleanup verification failed — project-scoped containers/volumes remain")
        if exit_code == 0:
            exit_code = 1

    if exit_code == 0:
        _log("browser E2E run complete — Playwright passed and cleanup verified.")
    return exit_code


def main(argv: list[str]) -> int:
    # Some Windows terminals default stdout to the console codepage, which
    # cannot encode this script's own em-dashes/ellipses — force UTF-8 so
    # log output never raises/garbles on any platform (matches
    # scripts/compose_smoke.py).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        config = parse_args(argv)
    except ValueError as exc:
        _log(f"invalid arguments: {exc}")
        return 2

    try:
        return run_browser_e2e(config)
    except BrowserE2EError as exc:
        _log(f"BROWSER E2E FAILURE: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
