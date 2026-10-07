#!/usr/bin/env python3
"""Lab-only controlled link-failure scenario harness (NPE-1C2, ADR-0003).

Runs ONE fixed scenario against an already-deployed Lab 1
(``containerlab deploy -t lab/topology.clab.yml``):

    healthy  -> leaf-1 eth1 down -> degraded -> leaf-1 eth1 up -> recovered

and measures BGP detection, FIB convergence, host reachability and recovery
from a single execution. It is a controlled fault injector for the local lab
only: the target container and interface are constants, and there is no
command-line option (or code path) that accepts another container, interface,
command, or address.

Standard library only. Intended to run inside Ubuntu WSL next to the native
Docker Engine (``python3 scripts/lab_failure_scenario.py``); progress goes to
stderr and a structured JSON result to stdout.

Exit codes:
    0  scenario passed
    1  scenario failed (degraded or recovered state did not match)
    2  refused: a safety precondition failed (nothing was injected)
    3  baseline unhealthy (nothing was injected)
    4  Docker/WSL runtime restarted during the scenario
    5  recovery failed: the lab may be left degraded
    6  unexpected internal error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_validate  # noqa: E402

EXIT_PASSED = 0
EXIT_SCENARIO_FAILED = 1
EXIT_REFUSED = 2
EXIT_BASELINE_UNHEALTHY = 3
EXIT_RUNTIME_RESTARTED = 4
EXIT_RECOVERY_FAILED = 5
EXIT_INTERNAL_ERROR = 6

SCENARIO = "leaf-1-eth1-spine-1-eth1-link-failure"

LAB_NAME = lab_validate.LAB_NAME
NODE_PREFIX = f"clab-{LAB_NAME}-"
EXPECTED_CONTAINERS = frozenset(
    NODE_PREFIX + node for node in (*lab_validate.ROUTERS, *lab_validate.HOSTS)
)

# The one and only fault target. Deliberately not configurable.
TARGET_CONTAINER = NODE_PREFIX + "leaf-1"
TARGET_INTERFACE = "eth1"
INJECT_ARGV: tuple[str, ...] = (
    "docker",
    "exec",
    TARGET_CONTAINER,
    "ip",
    "link",
    "set",
    TARGET_INTERFACE,
    "down",
)
RESTORE_ARGV: tuple[str, ...] = (
    "docker",
    "exec",
    TARGET_CONTAINER,
    "ip",
    "link",
    "set",
    TARGET_INTERFACE,
    "up",
)

# session name -> (leaf router, neighbor IP on the spine). Each physical
# leaf-spine session is observed from the leaf side.
SESSIONS: dict[str, tuple[str, str]] = {
    "leaf-1/spine-1": ("leaf-1", "10.255.0.0"),
    "leaf-1/spine-2": ("leaf-1", "10.255.0.2"),
    "leaf-2/spine-1": ("leaf-2", "10.255.0.4"),
    "leaf-2/spine-2": ("leaf-2", "10.255.0.6"),
}
FAILED_SESSION = "leaf-1/spine-1"

# leaf -> (remote host prefix, healthy next-hops, degraded next-hops)
ROUTE_CHECKS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "leaf-1": (
        "10.1.2.0/24",
        frozenset({"10.255.0.0 via eth1", "10.255.0.2 via eth2"}),
        frozenset({"10.255.0.2 via eth2"}),
    ),
    "leaf-2": (
        "10.1.1.0/24",
        frozenset({"10.255.0.4 via eth1", "10.255.0.6 via eth2"}),
        frozenset({"10.255.0.6 via eth2"}),
    ),
}

# (source host, destination host address)
HOST_PINGS: tuple[tuple[str, str], ...] = (
    ("host-1", "10.1.2.10"),
    ("host-2", "10.1.1.10"),
)
MONITOR_SOURCE, MONITOR_TARGET = HOST_PINGS[0]

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

POLL_INTERVAL_S = 0.05
DEGRADED_TIMEOUT_S = 30.0
RECOVERY_TIMEOUT_S = 60.0
PROBE_INTERVAL_S = 0.2
COMMAND_TIMEOUT_S = 20.0

MEASUREMENT_NOTE = (
    "Times are monotonic and measured by polling; each is an upper bound with "
    "a resolution of one poll cycle (several docker exec calls, typically a "
    "few hundred ms) and includes docker exec dispatch latency."
)

PHASES = (
    "BASELINE",
    "FAILURE_INJECTED",
    "DEGRADED",
    "RESTORING",
    "RECOVERED",
    "FAILED",
)


# --------------------------------------------------------------------------
# Command execution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(argv: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "command timed out")
    except OSError as error:
        return CommandResult(127, "", str(error))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class ScenarioError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class PreconditionError(ScenarioError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_REFUSED)


class BaselineError(ScenarioError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_BASELINE_UNHEALTHY)


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_NEXT_HOP_RE = re.compile(r"\bvia (\d{1,3}(?:\.\d{1,3}){3}) dev (\S+)")
_PING_RE = re.compile(r"(\d+) packets transmitted, (\d+) packets received")


def parse_next_hops(output: str) -> frozenset[str]:
    """Next-hops of ``ip route show <prefix>`` as ``"<ip> via <dev>"``."""
    return frozenset(f"{ip} via {dev}" for ip, dev in _NEXT_HOP_RE.findall(output))


def parse_bgp_peer_states(output: str) -> dict[str, str]:
    """Neighbor IP -> BGP state from ``show bgp summary json``."""
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return {}
    peers = (
        data.get("ipv4Unicast", {}).get("peers", {}) if isinstance(data, dict) else {}
    )
    return {
        str(ip): str(info.get("state", "Unknown"))
        for ip, info in peers.items()
        if isinstance(info, dict)
    }


def parse_ping(output: str) -> tuple[int, int] | None:
    match = _PING_RE.search(output)
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_key_values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


# --------------------------------------------------------------------------
# Lab command wrapper (read-only queries + the two fixed fault commands)
# --------------------------------------------------------------------------


class LabCommands:
    """The only place commands are built. Containers are checked against the
    fixed Lab 1 inventory; the fault commands are fixed constants."""

    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def _docker_exec(self, container: str, *argv: str) -> CommandResult:
        if container not in EXPECTED_CONTAINERS:
            raise PreconditionError(
                f"refusing to exec in {container!r}: not a Lab 1 node"
            )
        return self._runner(["docker", "exec", container, *argv])

    # -- fault commands (fixed) ------------------------------------------
    def inject_failure(self) -> CommandResult:
        return self._runner(list(INJECT_ARGV))

    def restore_link(self) -> CommandResult:
        return self._runner(list(RESTORE_ARGV))

    # -- environment -----------------------------------------------------
    def docker_operating_system(self) -> str | None:
        result = self._runner(["docker", "info", "--format", "{{.OperatingSystem}}"])
        return result.stdout.strip() if result.returncode == 0 else None

    def running_lab_containers(self) -> frozenset[str] | None:
        result = self._runner(["docker", "ps", "--format", "{{.Names}}"])
        if result.returncode != 0:
            return None
        return frozenset(
            name
            for name in (line.strip() for line in result.stdout.splitlines())
            if name.startswith(NODE_PREFIX)
        )

    def runtime_snapshot(self) -> dict[str, str] | None:
        boot = self._runner(["cat", BOOT_ID_PATH])
        service = self._runner(
            [
                "systemctl",
                "show",
                "docker",
                "-p",
                "NRestarts",
                "-p",
                "ActiveEnterTimestamp",
            ]
        )
        if boot.returncode != 0 or service.returncode != 0:
            return None
        values = parse_key_values(service.stdout)
        return {
            "boot_id": boot.stdout.strip(),
            "docker_nrestarts": values.get("NRestarts", ""),
            "docker_active_enter_timestamp": values.get("ActiveEnterTimestamp", ""),
        }

    # -- lab state -------------------------------------------------------
    def leaf_session_states(self, leaf: str) -> dict[str, str]:
        result = self._docker_exec(
            NODE_PREFIX + leaf, "vtysh", "-c", "show bgp summary json"
        )
        peers = parse_bgp_peer_states(result.stdout) if result.returncode == 0 else {}
        return {
            name: peers.get(ip, "Missing")
            for name, (router, ip) in SESSIONS.items()
            if router == leaf
        }

    def session_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for leaf in ("leaf-1", "leaf-2"):
            states.update(self.leaf_session_states(leaf))
        return states

    def next_hops(self, leaf: str) -> frozenset[str]:
        prefix = ROUTE_CHECKS[leaf][0]
        result = self._docker_exec(NODE_PREFIX + leaf, "ip", "route", "show", prefix)
        return parse_next_hops(result.stdout) if result.returncode == 0 else frozenset()

    def ping(self, host: str, target: str, count: int, wait_s: int) -> bool:
        result = self._docker_exec(
            NODE_PREFIX + host, "ping", "-c", str(count), "-W", str(wait_s), target
        )
        counts = parse_ping(result.stdout)
        return counts is not None and counts[0] == count and counts[1] == count

    def bidirectional_reachability(self) -> dict[str, bool]:
        return {
            f"{host}->{target}": self.ping(host, target, 3, 2)
            for host, target in HOST_PINGS
        }


# --------------------------------------------------------------------------
# Reachability monitor
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeSample:
    t: float
    ok: bool


def summarize_probes(
    samples: Sequence[ProbeSample], injected_at: float | None
) -> dict[str, Any]:
    failed = [s for s in samples if not s.ok]
    longest = current = 0
    for sample in samples:
        current = 0 if sample.ok else current + 1
        longest = max(longest, current)
    total = len(samples)
    return {
        "probes": total,
        "successful": total - len(failed),
        "failed": len(failed),
        "loss_percent": round(100.0 * len(failed) / total, 3) if total else None,
        "longest_failure_streak": longest,
        "failed_probe_offsets_ms": [
            round((s.t - injected_at) * 1000.0, 1) if injected_at is not None else s.t
            for s in failed
        ],
    }


class ReachabilityMonitor:
    """host-1 -> host-2 probes, one single-packet ping per probe, timestamped by
    this process (no dependence on BusyBox's buffered long-running output)."""

    def __init__(
        self,
        commands: LabCommands,
        clock: Callable[[], float] = time.monotonic,
        interval_s: float = PROBE_INTERVAL_S,
    ) -> None:
        self._commands = commands
        self._clock = clock
        self._interval_s = interval_s
        self._samples: list[ProbeSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def probe_once(self) -> ProbeSample:
        started = self._clock()
        ok = self._commands.ping(MONITOR_SOURCE, MONITOR_TARGET, 1, 1)
        sample = ProbeSample(started, ok)
        self._samples.append(sample)
        return sample

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.probe_once()
            self._stop.wait(self._interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> list[ProbeSample]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=COMMAND_TIMEOUT_S + 5)
        return list(self._samples)


# --------------------------------------------------------------------------
# Scenario
# --------------------------------------------------------------------------


def _established(states: dict[str, str]) -> int:
    return sum(1 for state in states.values() if state == "Established")


class LinkFailureScenario:
    def __init__(
        self,
        commands: LabCommands,
        monitor: Any,
        topology_path: Path,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cmd = commands
        self._monitor = monitor
        self._topology_path = topology_path
        self._clock = clock
        self._sleep = sleep
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda message: print(message, file=sys.stderr))
        self._t0 = clock()
        self._phases: list[dict[str, Any]] = []
        self._injected_at: float | None = None
        self._restored_at: float | None = None
        self._injected = False
        self._restore_error: str | None = None
        self._restore_ok = False
        self._result: dict[str, Any] = {
            "scenario": SCENARIO,
            "status": "unknown",
            "exit_code": EXIT_INTERNAL_ERROR,
            "failure_reason": None,
            "baseline": None,
            "failure": None,
            "degraded": None,
            "reachability_monitor": None,
            "recovery": None,
            "runtime": None,
            "phases": self._phases,
            "measurement_note": MEASUREMENT_NOTE,
        }
        self._runtime_before: dict[str, str] | None = None

    # -- bookkeeping -----------------------------------------------------
    def _phase(self, name: str) -> None:
        self._phases.append(
            {"phase": name, "t_ms": round((self._clock() - self._t0) * 1000.0, 1)}
        )
        self._log(f"[phase] {name}")

    def _ms_since(self, origin: float, observed_at: float | None) -> float | None:
        return (
            None if observed_at is None else round((observed_at - origin) * 1000.0, 1)
        )

    # -- polling ---------------------------------------------------------
    def _poll(
        self, checks: dict[str, Callable[[], bool]], origin: float, timeout_s: float
    ) -> dict[str, float | None]:
        """Evaluate each check every cycle until all are true or timeout.
        Returns ms from ``origin`` at which each first held (None if never)."""
        observed: dict[str, float | None] = {name: None for name in checks}
        deadline = self._clock() + timeout_s
        while True:
            for name, check in checks.items():
                if observed[name] is None and check():
                    observed[name] = self._ms_since(origin, self._clock())
            if all(value is not None for value in observed.values()):
                return observed
            if self._clock() >= deadline:
                return observed
            self._sleep(POLL_INTERVAL_S)

    # -- safety + baseline ------------------------------------------------
    def _preflight(self) -> None:
        try:
            topology = lab_validate.parse_topology(
                self._topology_path.read_text(encoding="utf-8")
            )
        except (OSError, lab_validate.TopologyParseError) as error:
            raise PreconditionError(f"cannot read Lab 1 topology: {error}") from error
        if topology.get("name") != LAB_NAME:
            raise PreconditionError(
                f"topology name must be {LAB_NAME!r}, got {topology.get('name')!r}"
            )

        operating_system = self._cmd.docker_operating_system()
        if operating_system is None:
            raise PreconditionError("Docker daemon is not reachable")
        if "docker desktop" in operating_system.lower():
            raise PreconditionError(
                f"Docker server is Docker Desktop ({operating_system!r}); "
                "this harness requires the native Ubuntu Docker Engine"
            )

        running = self._cmd.running_lab_containers()
        if running is None:
            raise PreconditionError("cannot list containers")
        if running != EXPECTED_CONTAINERS:
            missing = sorted(EXPECTED_CONTAINERS - running)
            extra = sorted(running - EXPECTED_CONTAINERS)
            raise PreconditionError(
                f"Lab 1 container inventory mismatch (missing={missing}, unexpected={extra})"
            )

        self._runtime_before = self._cmd.runtime_snapshot()
        if self._runtime_before is None:
            raise PreconditionError("cannot read the WSL/Docker runtime identity")

        self._phase("BASELINE")
        sessions = self._cmd.session_states()
        routes = {leaf: self._cmd.next_hops(leaf) for leaf in ROUTE_CHECKS}
        reachability = self._cmd.bidirectional_reachability()
        self._result["baseline"] = {
            "sessions": sessions,
            "established_sessions": _established(sessions),
            "leaf1_next_hops": sorted(routes["leaf-1"]),
            "leaf2_next_hops": sorted(routes["leaf-2"]),
            "leaf1_ecmp_paths": len(routes["leaf-1"]),
            "leaf2_ecmp_paths": len(routes["leaf-2"]),
            "reachability": reachability,
        }
        problems: list[str] = []
        if _established(sessions) != len(SESSIONS) or set(sessions) != set(SESSIONS):
            problems.append(f"BGP sessions not all Established: {sessions}")
        for leaf, (prefix, healthy, _) in ROUTE_CHECKS.items():
            if routes[leaf] != healthy:
                problems.append(
                    f"{leaf} next-hops for {prefix} are {sorted(routes[leaf])}, "
                    f"expected {sorted(healthy)}"
                )
        if not all(reachability.values()):
            problems.append(f"host reachability failed: {reachability}")
        if problems:
            raise BaselineError("; ".join(problems))

    # -- failure ----------------------------------------------------------
    def _inject(self) -> None:
        self._injected = True  # set first so cleanup runs even if the call raises
        self._injected_at = self._clock()
        injected_wall = self._wall()
        result = self._cmd.inject_failure()
        self._phase("FAILURE_INJECTED")
        self._result["failure"] = {
            "target": f"{TARGET_CONTAINER}:{TARGET_INTERFACE}",
            "failed_link": "leaf-1 eth1 <-> spine-1 eth1",
            "injected_at": self._injected_at,
            "injected_at_utc": injected_wall.isoformat(),
            "bgp_detection_ms": None,
            "leaf1_fib_convergence_ms": None,
            "leaf2_fib_convergence_ms": None,
        }
        if result.returncode != 0:
            raise ScenarioError(
                f"fault injection command failed: {result.stderr.strip()}",
                EXIT_SCENARIO_FAILED,
            )

    def _observe_degraded(self) -> None:
        assert self._injected_at is not None
        origin = self._injected_at
        degraded_hops = {leaf: ROUTE_CHECKS[leaf][2] for leaf in ROUTE_CHECKS}
        checks = {
            "bgp": lambda: self._cmd.leaf_session_states("leaf-1")[FAILED_SESSION]
            != "Established",
            "leaf1": lambda: self._cmd.next_hops("leaf-1") == degraded_hops["leaf-1"],
            "leaf2": lambda: self._cmd.next_hops("leaf-2") == degraded_hops["leaf-2"],
        }
        observed = self._poll(checks, origin, DEGRADED_TIMEOUT_S)
        failure = self._result["failure"]
        failure["bgp_detection_ms"] = observed["bgp"]
        failure["leaf1_fib_convergence_ms"] = observed["leaf1"]
        failure["leaf2_fib_convergence_ms"] = observed["leaf2"]

        self._phase("DEGRADED")
        sessions = self._cmd.session_states()
        routes = {leaf: self._cmd.next_hops(leaf) for leaf in ROUTE_CHECKS}
        reachability = self._cmd.bidirectional_reachability()
        self._result["degraded"] = {
            "sessions": sessions,
            "established_sessions": _established(sessions),
            "leaf1_next_hops": sorted(routes["leaf-1"]),
            "leaf2_next_hops": sorted(routes["leaf-2"]),
            "reachability": reachability,
        }
        problems = [
            f"{name} never reached the degraded state within {DEGRADED_TIMEOUT_S}s"
            for name, value in observed.items()
            if value is None
        ]
        if sessions.get(FAILED_SESSION) == "Established":
            problems.append(f"{FAILED_SESSION} is still Established")
        others = {k: v for k, v in sessions.items() if k != FAILED_SESSION}
        if set(others) != set(SESSIONS) - {FAILED_SESSION} or _established(others) != 3:
            problems.append(f"the other 3 sessions must stay Established: {others}")
        for leaf in ROUTE_CHECKS:
            if routes[leaf] != degraded_hops[leaf]:
                problems.append(
                    f"{leaf} degraded next-hops are {sorted(routes[leaf])}, "
                    f"expected {sorted(degraded_hops[leaf])}"
                )
        if not all(reachability.values()):
            problems.append(f"host reachability lost while degraded: {reachability}")
        if problems:
            raise ScenarioError("; ".join(problems), EXIT_SCENARIO_FAILED)

    # -- restore (always) ------------------------------------------------
    def _restore_if_injected(self) -> None:
        if not self._injected or self._restore_ok:
            return
        self._phase("RESTORING")
        self._restored_at = self._clock()
        try:
            result = self._cmd.restore_link()
        except Exception as error:  # noqa: BLE001 - must never mask the original error
            self._restore_error = f"restore command raised {error!r}"
            return
        if result.returncode != 0:
            self._restore_error = f"restore command failed: {result.stderr.strip()}"
            return
        self._restore_ok = True

    def _observe_recovery(self) -> None:
        assert self._restored_at is not None
        origin = self._restored_at
        healthy_hops = {leaf: ROUTE_CHECKS[leaf][1] for leaf in ROUTE_CHECKS}
        checks = {
            "bgp": lambda: self._cmd.leaf_session_states("leaf-1")[FAILED_SESSION]
            == "Established",
            "leaf1": lambda: self._cmd.next_hops("leaf-1") == healthy_hops["leaf-1"],
            "leaf2": lambda: self._cmd.next_hops("leaf-2") == healthy_hops["leaf-2"],
        }
        observed = self._poll(checks, origin, RECOVERY_TIMEOUT_S)

        sessions = self._cmd.session_states()
        routes = {leaf: self._cmd.next_hops(leaf) for leaf in ROUTE_CHECKS}
        reachability = self._cmd.bidirectional_reachability()
        self._result["recovery"] = {
            "restored_at": self._restored_at,
            "bgp_reestablish_ms": observed["bgp"],
            "leaf1_ecmp_restore_ms": observed["leaf1"],
            "leaf2_ecmp_restore_ms": observed["leaf2"],
            "sessions": sessions,
            "established_sessions": _established(sessions),
            "leaf1_next_hops": sorted(routes["leaf-1"]),
            "leaf2_next_hops": sorted(routes["leaf-2"]),
            "leaf1_ecmp_paths": len(routes["leaf-1"]),
            "leaf2_ecmp_paths": len(routes["leaf-2"]),
            "reachability": reachability,
        }
        problems = [
            f"{name} did not recover within {RECOVERY_TIMEOUT_S}s"
            for name, value in observed.items()
            if value is None
        ]
        if _established(sessions) != len(SESSIONS):
            problems.append(f"not all sessions Established after restore: {sessions}")
        for leaf in ROUTE_CHECKS:
            if routes[leaf] != healthy_hops[leaf]:
                problems.append(
                    f"{leaf} next-hops after restore are {sorted(routes[leaf])}, "
                    f"expected {sorted(healthy_hops[leaf])}"
                )
        if not all(reachability.values()):
            problems.append(f"host reachability failed after restore: {reachability}")
        if problems:
            raise ScenarioError("; ".join(problems), EXIT_RECOVERY_FAILED)
        self._phase("RECOVERED")

    # -- driver ------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        failure: ScenarioError | None = None
        monitor_started = False
        try:
            self._preflight()
        except ScenarioError as error:
            return self._finish(error, [])
        except Exception as error:  # noqa: BLE001
            return self._finish(
                ScenarioError(
                    f"unexpected error in preflight: {error!r}", EXIT_INTERNAL_ERROR
                ),
                [],
            )

        try:
            self._monitor.start()
            monitor_started = True
            self._inject()
            self._observe_degraded()
        except ScenarioError as error:
            failure = error
        except Exception as error:  # noqa: BLE001
            failure = ScenarioError(f"unexpected error: {error!r}", EXIT_INTERNAL_ERROR)
        finally:
            self._restore_if_injected()

        if self._injected:
            if self._restore_error is not None:
                failure = ScenarioError(self._restore_error, EXIT_RECOVERY_FAILED)
            else:
                try:
                    self._observe_recovery()
                except ScenarioError as error:
                    # A recovery failure outranks an earlier degraded-state failure.
                    if failure is None or error.exit_code == EXIT_RECOVERY_FAILED:
                        failure = error
                except Exception as error:  # noqa: BLE001
                    failure = ScenarioError(
                        f"unexpected error during recovery: {error!r}",
                        EXIT_INTERNAL_ERROR,
                    )

        samples = self._monitor.stop() if monitor_started else []
        return self._finish(failure, samples)

    def _finish(
        self, failure: ScenarioError | None, samples: Sequence[ProbeSample]
    ) -> dict[str, Any]:
        runtime_after = self._cmd.runtime_snapshot() if self._runtime_before else None
        before = self._runtime_before
        restarted = bool(
            before is not None and runtime_after is not None and runtime_after != before
        )
        self._result["runtime"] = {
            "boot_id_before": before["boot_id"] if before else None,
            "boot_id_after": runtime_after["boot_id"] if runtime_after else None,
            "docker_nrestarts_before": before["docker_nrestarts"] if before else None,
            "docker_nrestarts_after": runtime_after["docker_nrestarts"]
            if runtime_after
            else None,
            "docker_active_enter_before": before["docker_active_enter_timestamp"]
            if before
            else None,
            "docker_active_enter_after": (
                runtime_after["docker_active_enter_timestamp"]
                if runtime_after
                else None
            ),
            "restarted": restarted,
        }
        if self._injected:
            self._result["reachability_monitor"] = summarize_probes(
                samples, self._injected_at
            )

        exit_code = failure.exit_code if failure else EXIT_PASSED
        reason = str(failure) if failure else None
        if restarted:
            reason = (
                reason + "; " if reason else ""
            ) + "Docker/WSL runtime restarted during the scenario"
            if exit_code in (EXIT_PASSED, EXIT_SCENARIO_FAILED):
                exit_code = EXIT_RUNTIME_RESTARTED
        statuses = {
            EXIT_PASSED: "passed",
            EXIT_SCENARIO_FAILED: "failed",
            EXIT_REFUSED: "refused",
            EXIT_BASELINE_UNHEALTHY: "baseline_unhealthy",
            EXIT_RUNTIME_RESTARTED: "runtime_restarted",
            EXIT_RECOVERY_FAILED: "recovery_failed",
            EXIT_INTERNAL_ERROR: "internal_error",
        }
        if exit_code != EXIT_PASSED:
            self._phase("FAILED")
        self._result["status"] = statuses[exit_code]
        self._result["exit_code"] = exit_code
        self._result["failure_reason"] = reason
        return self._result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Lab-only controlled failure of leaf-1 eth1 <-> spine-1 eth1 in the "
            "deployed Lab 1, with recovery and measurements. The target is fixed."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON result to this file",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    runner: Runner | None = None,
    topology_path: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    commands = LabCommands(runner or subprocess_runner)
    scenario = LinkFailureScenario(
        commands,
        ReachabilityMonitor(commands),
        topology_path
        or Path(__file__).resolve().parent.parent / "lab" / "topology.clab.yml",
    )
    result = scenario.run()
    text = json.dumps(result, indent=2, sort_keys=False)
    print(text)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
