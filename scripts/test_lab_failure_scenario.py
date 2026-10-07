#!/usr/bin/env python3
"""Unit tests for scripts/lab_failure_scenario.py (NPE-1C2).

Standard-library ``unittest`` only. No Docker, Containerlab, or running lab is
needed: every command goes through a simulated lab (``FakeLab``) with a fake
clock, so convergence timing, failures, and restarts are deterministic.

Run directly:
    python scripts/test_lab_failure_scenario.py
"""

from __future__ import annotations

import io
import ipaddress
import json
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_failure_scenario as lfs  # noqa: E402
import lab_validate  # noqa: E402

TOPOLOGY = Path(__file__).resolve().parent.parent / "lab" / "topology.clab.yml"
INF = float("inf")

HEALTHY_L1 = {"10.255.0.0 via eth1", "10.255.0.2 via eth2"}
HEALTHY_L2 = {"10.255.0.4 via eth1", "10.255.0.6 via eth2"}
DEGRADED_L1 = {"10.255.0.2 via eth2"}
DEGRADED_L2 = {"10.255.0.6 via eth2"}


def route_text(prefix: str, hops: set[str]) -> str:
    entries = sorted(hop.split(" via ") for hop in hops)
    if len(entries) == 1:
        ip, dev = entries[0]
        return f"{prefix} nhid 9 via {ip} dev {dev} proto bgp metric 20 \n"
    lines = [f"{prefix} nhid 31 proto bgp metric 20 \n"]
    lines += [f"\tnexthop via {ip} dev {dev} weight 1 \n" for ip, dev in entries]
    return "".join(lines)


class FakeLab:
    """A simulated Lab 1 driven by a fake clock.

    ``delays`` map each observable to (seconds after link-down until it
    degrades, seconds after link-up until it recovers); ``INF`` means never.
    """

    def __init__(
        self,
        *,
        containers: Sequence[str] | None = None,
        docker_os: str = "Ubuntu 22.04.3 LTS",
        bgp: tuple[float, float] = (0.4, 2.0),
        leaf1_fib: tuple[float, float] = (0.3, 2.2),
        leaf2_fib: tuple[float, float] = (0.6, 2.3),
        baseline_state: str = "Established",
        healthy_l1: set[str] | None = None,
        degraded_l1: set[str] | None = None,
        host_ping_fail: set[str] | None = None,
        degraded_ping_fail: bool = False,
        raise_on_vtysh_while_down: bool = False,
        restore_returncode: int = 0,
    ) -> None:
        self.now = 1000.0
        self.commands: list[tuple[str, ...]] = []
        self.containers = list(
            containers if containers is not None else sorted(lfs.EXPECTED_CONTAINERS)
        )
        self.docker_os = docker_os
        self.bgp, self.leaf1_fib, self.leaf2_fib = bgp, leaf1_fib, leaf2_fib
        self.baseline_state = baseline_state
        self.healthy_l1 = healthy_l1 if healthy_l1 is not None else set(HEALTHY_L1)
        self.degraded_l1 = degraded_l1 if degraded_l1 is not None else set(DEGRADED_L1)
        self.host_ping_fail = host_ping_fail or set()
        self.degraded_ping_fail = degraded_ping_fail
        self.raise_on_vtysh_while_down = raise_on_vtysh_while_down
        self.restore_returncode = restore_returncode
        self.boot_id = "boot-A"
        self.nrestarts = "0"
        self.down_at: float | None = None
        self.up_at: float | None = None
        self.on_restore: Callable[[], None] | None = None

    # fake time ---------------------------------------------------------
    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    # simulation --------------------------------------------------------
    def _degraded(self, delays: tuple[float, float]) -> bool:
        degrade, recover = delays
        if self.down_at is None or self.now < self.down_at + degrade:
            return False
        return not (self.up_at is not None and self.now >= self.up_at + recover)

    def _link_down(self) -> bool:
        return self.down_at is not None and self.up_at is None

    def _bgp_json(self, leaf: str) -> str:
        peers = {
            ip: {"state": "Established"}
            for name, (router, ip) in lfs.SESSIONS.items()
            if router == leaf
        }
        if leaf == "leaf-1":
            if self.down_at is None:
                peers["10.255.0.0"]["state"] = self.baseline_state
            elif self._degraded(self.bgp):
                peers["10.255.0.0"]["state"] = "Active"
        warning = "% Can't open configuration file /etc/frr/vtysh.conf\n"
        return warning + json.dumps({"ipv4Unicast": {"peers": peers}})

    def _hops(self, leaf: str) -> set[str]:
        if leaf == "leaf-1":
            return (
                self.degraded_l1 if self._degraded(self.leaf1_fib) else self.healthy_l1
            )
        return DEGRADED_L2 if self._degraded(self.leaf2_fib) else HEALTHY_L2

    def runner(self, argv: Sequence[str]) -> lfs.CommandResult:
        self.commands.append(tuple(argv))
        self.now += 0.01
        a = list(argv)
        if a[:2] == ["docker", "info"]:
            return lfs.CommandResult(0, self.docker_os + "\n")
        if a[:2] == ["docker", "ps"]:
            return lfs.CommandResult(0, "\n".join(self.containers) + "\n")
        if a[:1] == ["cat"]:
            return lfs.CommandResult(0, self.boot_id + "\n")
        if a[:3] == ["systemctl", "show", "docker"]:
            return lfs.CommandResult(
                0, f"NRestarts={self.nrestarts}\nActiveEnterTimestamp=ts-1\n"
            )
        if tuple(a) == lfs.INJECT_ARGV:
            self.down_at = self.now
            return lfs.CommandResult(0)
        if tuple(a) == lfs.RESTORE_ARGV:
            if self.restore_returncode == 0:
                self.up_at = self.now
            if self.on_restore is not None:
                self.on_restore()
            return lfs.CommandResult(self.restore_returncode, "", "ip: failed")
        container = a[2]
        leaf = container.removeprefix(lfs.NODE_PREFIX)
        if a[3] == "vtysh":
            if self.raise_on_vtysh_while_down and self._link_down():
                raise RuntimeError("simulated vtysh crash")
            return lfs.CommandResult(0, self._bgp_json(leaf))
        if a[3:5] == ["ip", "route"]:
            return lfs.CommandResult(0, route_text(a[6], self._hops(leaf)))
        if a[3] == "ping":
            count = int(a[5])
            if leaf in self.host_ping_fail or (
                self.degraded_ping_fail and self._link_down() and leaf == "host-2"
            ):
                return lfs.CommandResult(
                    1,
                    f"{count} packets transmitted, 0 packets received, 100% packet loss\n",
                )
            return lfs.CommandResult(
                0,
                f"{count} packets transmitted, {count} packets received, 0% packet loss\n",
            )
        raise AssertionError(f"unexpected command {a}")


class FakeMonitor:
    def __init__(self, samples: Sequence[lfs.ProbeSample] = ()) -> None:
        self.samples = list(samples)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> list[lfs.ProbeSample]:
        self.stopped = True
        return self.samples


def make_scenario(
    lab: FakeLab,
    monitor: FakeMonitor | None = None,
    topology: Path = TOPOLOGY,
) -> lfs.LinkFailureScenario:
    return lfs.LinkFailureScenario(
        lfs.LabCommands(lab.runner),
        monitor if monitor is not None else FakeMonitor(),
        topology,
        clock=lab.clock,
        sleep=lab.sleep,
        wall_clock=lambda: datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc),
        log=lambda message: None,
    )


class HealthyScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = FakeLab()
        self.monitor = FakeMonitor()
        self.result = make_scenario(self.lab, self.monitor).run()

    def test_scenario__passes_with_exit_zero(self) -> None:
        self.assertEqual(self.result["status"], "passed")
        self.assertEqual(self.result["exit_code"], lfs.EXIT_PASSED)
        self.assertIsNone(self.result["failure_reason"])
        self.assertTrue(self.monitor.started and self.monitor.stopped)

    def test_baseline_healthy__accepted(self) -> None:
        baseline = self.result["baseline"]
        self.assertEqual(baseline["established_sessions"], 4)
        self.assertEqual(baseline["leaf1_ecmp_paths"], 2)
        self.assertEqual(baseline["leaf2_ecmp_paths"], 2)
        self.assertTrue(all(baseline["reachability"].values()))

    def test_degraded_two_to_one_recognized(self) -> None:
        degraded = self.result["degraded"]
        self.assertEqual(degraded["established_sessions"], 3)
        self.assertEqual(degraded["sessions"][lfs.FAILED_SESSION], "Active")
        self.assertEqual(degraded["leaf1_next_hops"], sorted(DEGRADED_L1))
        self.assertEqual(degraded["leaf2_next_hops"], sorted(DEGRADED_L2))
        self.assertTrue(all(degraded["reachability"].values()))

    def test_recovery_one_to_two_recognized(self) -> None:
        recovery = self.result["recovery"]
        self.assertEqual(recovery["established_sessions"], 4)
        self.assertEqual(recovery["leaf1_ecmp_paths"], 2)
        self.assertEqual(recovery["leaf2_ecmp_paths"], 2)
        self.assertEqual(recovery["leaf1_next_hops"], sorted(HEALTHY_L1))
        self.assertEqual(recovery["leaf2_next_hops"], sorted(HEALTHY_L2))
        self.assertTrue(all(recovery["reachability"].values()))

    def test_measurements__are_independent_and_follow_the_simulated_delays(
        self,
    ) -> None:
        failure, recovery = self.result["failure"], self.result["recovery"]
        for key, delay in (
            ("bgp_detection_ms", 400),
            ("leaf1_fib_convergence_ms", 300),
            ("leaf2_fib_convergence_ms", 600),
        ):
            self.assertGreaterEqual(failure[key], delay, key)
            self.assertLess(failure[key], delay + 500, key)
        for key, delay in (
            ("bgp_reestablish_ms", 2000),
            ("leaf1_ecmp_restore_ms", 2200),
            ("leaf2_ecmp_restore_ms", 2300),
        ):
            self.assertGreaterEqual(recovery[key], delay, key)
            self.assertLess(recovery[key], delay + 500, key)
        self.assertEqual(
            len(
                {
                    failure["bgp_detection_ms"],
                    failure["leaf1_fib_convergence_ms"],
                    failure["leaf2_fib_convergence_ms"],
                }
            ),
            3,
        )

    def test_phases__recorded_in_order_with_monotonic_timestamps(self) -> None:
        names = [p["phase"] for p in self.result["phases"]]
        self.assertEqual(
            names,
            ["BASELINE", "FAILURE_INJECTED", "DEGRADED", "RESTORING", "RECOVERED"],
        )
        times = [p["t_ms"] for p in self.result["phases"]]
        self.assertEqual(times, sorted(times))

    def test_exact_failure_command_emitted_once_and_restored_once(self) -> None:
        self.assertEqual(self.lab.commands.count(lfs.INJECT_ARGV), 1)
        self.assertEqual(self.lab.commands.count(lfs.RESTORE_ARGV), 1)
        self.assertEqual(
            lfs.INJECT_ARGV,
            (
                "docker",
                "exec",
                "clab-meta-rne-bgp-leaf-1",
                "ip",
                "link",
                "set",
                "eth1",
                "down",
            ),
        )
        self.assertEqual(
            [c for c in self.lab.commands if "link" in c],
            [lfs.INJECT_ARGV, lfs.RESTORE_ARGV],
        )

    def test_only_lab_1_nodes_are_ever_exec_targets(self) -> None:
        targets = {c[2] for c in self.lab.commands if c[:2] == ("docker", "exec")}
        self.assertTrue(targets <= lfs.EXPECTED_CONTAINERS)

    def test_result__json_serializable_with_required_schema(self) -> None:
        decoded = json.loads(json.dumps(self.result))
        for key in (
            "scenario",
            "status",
            "baseline",
            "failure",
            "degraded",
            "reachability_monitor",
            "recovery",
            "runtime",
        ):
            self.assertIn(key, decoded)
        self.assertEqual(
            set(decoded["failure"]),
            {
                "target",
                "failed_link",
                "injected_at",
                "injected_at_utc",
                "bgp_detection_ms",
                "leaf1_fib_convergence_ms",
                "leaf2_fib_convergence_ms",
            },
        )
        self.assertEqual(decoded["failure"]["target"], "clab-meta-rne-bgp-leaf-1:eth1")
        for key in (
            "bgp_reestablish_ms",
            "leaf1_ecmp_restore_ms",
            "leaf2_ecmp_restore_ms",
            "established_sessions",
            "leaf1_ecmp_paths",
            "leaf2_ecmp_paths",
        ):
            self.assertIn(key, decoded["recovery"])
        for key in (
            "boot_id_before",
            "boot_id_after",
            "docker_nrestarts_before",
            "docker_nrestarts_after",
        ):
            self.assertIn(key, decoded["runtime"])
        for key in (
            "probes",
            "successful",
            "failed",
            "loss_percent",
            "longest_failure_streak",
            "failed_probe_offsets_ms",
        ):
            self.assertIn(key, decoded["reachability_monitor"])

    def test_runtime_unchanged__not_flagged(self) -> None:
        runtime = self.result["runtime"]
        self.assertFalse(runtime["restarted"])
        self.assertEqual(runtime["boot_id_before"], runtime["boot_id_after"])


class RefusalTests(unittest.TestCase):
    def assert_refused_before_injection(
        self, lab: FakeLab, exit_code: int, monitor: FakeMonitor | None = None
    ) -> dict[str, Any]:
        monitor = monitor or FakeMonitor()
        result = make_scenario(lab, monitor).run()
        self.assertEqual(result["exit_code"], exit_code, result["failure_reason"])
        self.assertNotIn(lfs.INJECT_ARGV, lab.commands)
        self.assertNotIn(lfs.RESTORE_ARGV, lab.commands)
        self.assertFalse(monitor.started)
        return result

    def test_unhealthy_bgp_baseline__rejected(self) -> None:
        result = self.assert_refused_before_injection(
            FakeLab(baseline_state="Active"), lfs.EXIT_BASELINE_UNHEALTHY
        )
        self.assertEqual(result["status"], "baseline_unhealthy")
        self.assertEqual(result["baseline"]["established_sessions"], 3)

    def test_baseline_ecmp_not_two__rejected(self) -> None:
        result = self.assert_refused_before_injection(
            FakeLab(healthy_l1={"10.255.0.2 via eth2"}), lfs.EXIT_BASELINE_UNHEALTHY
        )
        self.assertEqual(result["baseline"]["leaf1_ecmp_paths"], 1)

    def test_baseline_host_unreachable__rejected(self) -> None:
        self.assert_refused_before_injection(
            FakeLab(host_ping_fail={"host-2"}), lfs.EXIT_BASELINE_UNHEALTHY
        )

    def test_wrong_container_inventory__rejected(self) -> None:
        full = sorted(lfs.EXPECTED_CONTAINERS)
        cases = {
            "missing node": full[1:],
            "no nodes": [],
            "unexpected extra lab container": [*full, "clab-meta-rne-bgp-spine-3"],
        }
        for label, containers in cases.items():
            with self.subTest(label):
                result = self.assert_refused_before_injection(
                    FakeLab(containers=containers), lfs.EXIT_REFUSED
                )
                self.assertEqual(result["status"], "refused")
                self.assertIn("inventory mismatch", result["failure_reason"])

    def test_unrelated_containers_are_ignored_not_rejected(self) -> None:
        lab = FakeLab(
            containers=[*sorted(lfs.EXPECTED_CONTAINERS), "welltelemetry-postgres"]
        )
        self.assertEqual(make_scenario(lab).run()["exit_code"], lfs.EXIT_PASSED)

    def test_docker_desktop_server__rejected(self) -> None:
        result = self.assert_refused_before_injection(
            FakeLab(docker_os="Docker Desktop"), lfs.EXIT_REFUSED
        )
        self.assertIn("Docker Desktop", result["failure_reason"])

    def test_wrong_topology_name__rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topology.clab.yml"
            path.write_text(
                "name: production\n\ntopology:\n  nodes:\n    a:\n", "utf-8"
            )
            lab = FakeLab()
            result = make_scenario(lab, topology=path).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_REFUSED)
        self.assertNotIn(lfs.INJECT_ARGV, lab.commands)

    def test_missing_topology__rejected(self) -> None:
        lab = FakeLab()
        result = make_scenario(lab, topology=Path("does-not-exist.yml")).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_REFUSED)


class TargetRestrictionTests(unittest.TestCase):
    def test_cli__offers_no_way_to_choose_target_container_interface_or_command(
        self,
    ) -> None:
        parser = lfs.build_parser()
        options = {o for action in parser._actions for o in action.option_strings}
        self.assertEqual(options, {"-h", "--help", "--output"})
        for flag in ("--container", "--interface", "--command", "--target", "--host"):
            with self.subTest(flag), self.assertRaises(SystemExit):
                with (
                    redirect_stdout(io.StringIO()),
                    mock.patch("sys.stderr", io.StringIO()),
                ):
                    parser.parse_args([flag, "x"])

    def test_exec_outside_lab_1_is_refused(self) -> None:
        commands = lfs.LabCommands(FakeLab().runner)
        for container in (
            "clab-prod-core-1",
            "welltelemetry-postgres",
            "clab-meta-rne-bgp-spine-9",
        ):
            with self.subTest(container), self.assertRaises(lfs.PreconditionError):
                commands._docker_exec(container, "ip", "link")

    def test_fault_target_is_the_fixed_leaf_1_eth1(self) -> None:
        self.assertEqual(lfs.TARGET_CONTAINER, "clab-meta-rne-bgp-leaf-1")
        self.assertEqual(lfs.TARGET_INTERFACE, "eth1")
        self.assertEqual(lfs.INJECT_ARGV[-3:], ("set", "eth1", "down"))
        self.assertEqual(lfs.RESTORE_ARGV[-3:], ("set", "eth1", "up"))


class FailurePathTests(unittest.TestCase):
    def test_wrong_degraded_next_hop__rejected_and_link_restored(self) -> None:
        lab = FakeLab(degraded_l1={"10.255.0.0 via eth1"})
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_SCENARIO_FAILED)
        self.assertEqual(result["status"], "failed")
        self.assertIn("leaf-1 degraded next-hops", result["failure_reason"])
        self.assertIsNone(result["failure"]["leaf1_fib_convergence_ms"])
        self.assertIn(lfs.RESTORE_ARGV, lab.commands)

    def test_degraded_timeout__still_restores_eth1(self) -> None:
        lab = FakeLab(bgp=(INF, 0.0))  # BGP never notices the failure
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_SCENARIO_FAILED)
        self.assertIn("never reached the degraded state", result["failure_reason"])
        self.assertIsNone(result["failure"]["bgp_detection_ms"])
        self.assertEqual(lab.commands.count(lfs.RESTORE_ARGV), 1)
        self.assertGreater(
            lab.commands.index(lfs.RESTORE_ARGV), lab.commands.index(lfs.INJECT_ARGV)
        )
        self.assertIsNotNone(result["recovery"])

    def test_exception_mid_scenario__restore_command_still_executes(self) -> None:
        lab = FakeLab(raise_on_vtysh_while_down=True)
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_INTERNAL_ERROR)
        self.assertEqual(result["status"], "internal_error")
        self.assertIn("simulated vtysh crash", result["failure_reason"])
        self.assertIn(lfs.RESTORE_ARGV, lab.commands)

    def test_keyboard_interrupt__still_restores_eth1(self) -> None:
        lab = FakeLab()
        original = lab.runner

        def interrupting(argv: Sequence[str]) -> lfs.CommandResult:
            if lab.down_at is not None and lab.up_at is None and "vtysh" in argv:
                raise KeyboardInterrupt
            return original(argv)

        scenario = lfs.LinkFailureScenario(
            lfs.LabCommands(interrupting),
            FakeMonitor(),
            TOPOLOGY,
            clock=lab.clock,
            sleep=lab.sleep,
            log=lambda m: None,
        )
        with self.assertRaises(KeyboardInterrupt):
            scenario.run()
        self.assertIn(lfs.RESTORE_ARGV, lab.commands)

    def test_degraded_reachability_lost__fails_scenario(self) -> None:
        lab = FakeLab(degraded_ping_fail=True)
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_SCENARIO_FAILED)
        self.assertIn("reachability lost while degraded", result["failure_reason"])
        self.assertIn(lfs.RESTORE_ARGV, lab.commands)

    def test_recovery_never_completes__exit_recovery_failed(self) -> None:
        lab = FakeLab(bgp=(0.4, INF))
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_RECOVERY_FAILED)
        self.assertEqual(result["status"], "recovery_failed")
        self.assertIsNone(result["recovery"]["bgp_reestablish_ms"])

    def test_restore_command_failure__exit_recovery_failed(self) -> None:
        lab = FakeLab(restore_returncode=1)
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_RECOVERY_FAILED)
        self.assertIn("restore command failed", result["failure_reason"])

    def test_runtime_restart_during_scenario__detected(self) -> None:
        lab = FakeLab()

        def restart() -> None:
            lab.boot_id = "boot-B"

        lab.on_restore = restart
        result = make_scenario(lab).run()
        self.assertEqual(result["exit_code"], lfs.EXIT_RUNTIME_RESTARTED)
        self.assertEqual(result["status"], "runtime_restarted")
        runtime = result["runtime"]
        self.assertTrue(runtime["restarted"])
        self.assertEqual(
            (runtime["boot_id_before"], runtime["boot_id_after"]), ("boot-A", "boot-B")
        )

    def test_docker_restart_count_change__detected(self) -> None:
        lab = FakeLab()

        def bump() -> None:
            lab.nrestarts = "1"

        lab.on_restore = bump
        result = make_scenario(lab).run()
        self.assertTrue(result["runtime"]["restarted"])
        self.assertEqual(result["exit_code"], lfs.EXIT_RUNTIME_RESTARTED)


class ReachabilityAccountingTests(unittest.TestCase):
    def test_summarize__zero_loss(self) -> None:
        samples = [lfs.ProbeSample(float(i), True) for i in range(10)]
        summary = lfs.summarize_probes(samples, injected_at=3.0)
        self.assertEqual(
            (summary["probes"], summary["successful"], summary["failed"]), (10, 10, 0)
        )
        self.assertEqual(summary["loss_percent"], 0.0)
        self.assertEqual(summary["longest_failure_streak"], 0)
        self.assertEqual(summary["failed_probe_offsets_ms"], [])

    def test_summarize__packet_loss_streak_and_offsets(self) -> None:
        flags = [True, True, False, False, False, True, False, True, True, True]
        samples = [lfs.ProbeSample(10.0 + 0.5 * i, ok) for i, ok in enumerate(flags)]
        summary = lfs.summarize_probes(samples, injected_at=11.0)
        self.assertEqual(summary["probes"], 10)
        self.assertEqual(summary["failed"], 4)
        self.assertEqual(summary["successful"], 6)
        self.assertEqual(summary["loss_percent"], 40.0)
        self.assertEqual(summary["longest_failure_streak"], 3)
        self.assertEqual(
            summary["failed_probe_offsets_ms"], [0.0, 500.0, 1000.0, 2000.0]
        )

    def test_summarize__no_probes(self) -> None:
        summary = lfs.summarize_probes([], injected_at=1.0)
        self.assertEqual(summary["probes"], 0)
        self.assertIsNone(summary["loss_percent"])

    def test_scenario__reports_monitor_loss(self) -> None:
        lab = FakeLab()
        samples = [
            lfs.ProbeSample(lab.now + i * 0.2, i not in (3, 4)) for i in range(20)
        ]
        result = make_scenario(lab, FakeMonitor(samples)).run()
        monitor = result["reachability_monitor"]
        self.assertEqual((monitor["probes"], monitor["failed"]), (20, 2))
        self.assertEqual(monitor["loss_percent"], 10.0)
        self.assertEqual(monitor["longest_failure_streak"], 2)
        self.assertEqual(
            result["exit_code"], lfs.EXIT_PASSED, "transient loss alone must not fail"
        )

    def test_scenario__zero_loss_monitor(self) -> None:
        lab = FakeLab()
        samples = [lfs.ProbeSample(lab.now + i * 0.2, True) for i in range(50)]
        monitor = make_scenario(lab, FakeMonitor(samples)).run()["reachability_monitor"]
        self.assertEqual((monitor["successful"], monitor["failed"]), (50, 0))
        self.assertEqual(monitor["loss_percent"], 0.0)

    def test_real_monitor__probe_once_uses_single_packet_ping_and_records(self) -> None:
        lab = FakeLab()
        monitor = lfs.ReachabilityMonitor(lfs.LabCommands(lab.runner), clock=lab.clock)
        sample = monitor.probe_once()
        self.assertTrue(sample.ok)
        ping = lab.commands[-1]
        self.assertEqual(
            ping,
            (
                "docker",
                "exec",
                "clab-meta-rne-bgp-host-1",
                "ping",
                "-c",
                "1",
                "-W",
                "1",
                "10.1.2.10",
            ),
        )
        lab.host_ping_fail = {"host-1"}
        self.assertFalse(monitor.probe_once().ok)

    def test_real_monitor__background_thread_collects_probes_then_stops(self) -> None:
        lab = FakeLab()
        monitor = lfs.ReachabilityMonitor(
            lfs.LabCommands(lab.runner), clock=lab.clock, interval_s=0.001
        )
        monitor.start()
        deadline = 200
        while len(monitor._samples) < 3 and deadline:
            deadline -= 1
            time.sleep(0.01)
        samples = monitor.stop()
        self.assertGreaterEqual(len(samples), 3)
        count = len(samples)
        time.sleep(0.01)
        self.assertEqual(
            len(monitor._samples), count, "monitor kept running after stop()"
        )


class ParserTests(unittest.TestCase):
    def test_parse_next_hops__ecmp_and_single(self) -> None:
        self.assertEqual(
            lfs.parse_next_hops(route_text("10.1.2.0/24", HEALTHY_L1)),
            frozenset(HEALTHY_L1),
        )
        self.assertEqual(
            lfs.parse_next_hops(route_text("10.1.2.0/24", DEGRADED_L1)),
            frozenset(DEGRADED_L1),
        )
        self.assertEqual(lfs.parse_next_hops(""), frozenset())

    def test_parse_bgp_peer_states__ignores_vtysh_warning_noise(self) -> None:
        text = "% warning\n" + json.dumps(
            {"ipv4Unicast": {"peers": {"10.255.0.0": {"state": "Active"}}}}
        )
        self.assertEqual(lfs.parse_bgp_peer_states(text), {"10.255.0.0": "Active"})
        self.assertEqual(lfs.parse_bgp_peer_states("not json"), {})
        self.assertEqual(lfs.parse_bgp_peer_states("{bad"), {})

    def test_parse_ping(self) -> None:
        self.assertEqual(
            lfs.parse_ping(
                "5 packets transmitted, 4 packets received, 20% packet loss"
            ),
            (5, 4),
        )
        self.assertIsNone(lfs.parse_ping("ping: bad address"))


class StaticContractTests(unittest.TestCase):
    """The harness constants must agree with the committed Lab 1 contract."""

    def test_sessions_match_the_committed_bgp_neighbors(self) -> None:
        for name, (leaf, ip) in lfs.SESSIONS.items():
            self.assertIn(ip, lab_validate.ROUTER_SPECS[leaf].neighbors, name)
        self.assertEqual(
            len(lfs.SESSIONS),
            sum(
                len(lab_validate.ROUTER_SPECS[leaf].neighbors)
                for leaf in ("leaf-1", "leaf-2")
            ),
        )

    def test_expected_next_hops_are_spine_addresses_on_the_leaf_links(self) -> None:
        spine_addresses = {
            ipaddress.ip_interface(address).ip
            for spine in ("spine-1", "spine-2")
            for address in lab_validate.ROUTER_SPECS[spine].interfaces.values()
        }
        for leaf, (_, healthy, degraded) in lfs.ROUTE_CHECKS.items():
            leaf_links = {
                dev: ipaddress.ip_interface(addr).network
                for dev, addr in lab_validate.ROUTER_SPECS[leaf].interfaces.items()
            }
            self.assertTrue(degraded < healthy)
            for hop in healthy:
                ip, dev = hop.split(" via ")
                self.assertIn(ipaddress.ip_address(ip), spine_addresses, hop)
                self.assertIn(ipaddress.ip_address(ip), leaf_links[dev], hop)

    def test_failed_session_is_the_leaf_1_eth1_spine_1_link(self) -> None:
        leaf, ip = lfs.SESSIONS[lfs.FAILED_SESSION]
        self.assertEqual(leaf, "leaf-1")
        eth1 = ipaddress.ip_interface(
            lab_validate.ROUTER_SPECS["leaf-1"].interfaces["eth1"]
        )
        spine_eth1 = ipaddress.ip_interface(
            lab_validate.ROUTER_SPECS["spine-1"].interfaces["eth1"]
        )
        self.assertEqual(ipaddress.ip_address(ip), spine_eth1.ip)
        self.assertEqual(eth1.network, spine_eth1.network)

    def test_expected_containers_are_the_six_lab_nodes(self) -> None:
        self.assertEqual(len(lfs.EXPECTED_CONTAINERS), 6)
        self.assertIn(lfs.TARGET_CONTAINER, lfs.EXPECTED_CONTAINERS)

    def test_host_pings_match_committed_host_addresses(self) -> None:
        self.assertEqual(
            dict(lfs.HOST_PINGS),
            {"host-1": "10.1.2.10", "host-2": "10.1.1.10"},
        )
        for host, exec_lines in lab_validate.EXPECTED_HOST_EXEC.items():
            self.assertTrue(
                any(line.startswith("ip addr add 10.1.") for line in exec_lines), host
            )


class MainTests(unittest.TestCase):
    def test_main__prints_json_returns_exit_code_and_writes_output(self) -> None:
        lab = FakeLab(bgp=(0.01, 0.02), leaf1_fib=(0.01, 0.02), leaf2_fib=(0.01, 0.02))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            buffer = io.StringIO()
            with mock.patch.object(
                lfs, "ReachabilityMonitor", lambda *a, **k: FakeMonitor()
            ):
                with redirect_stdout(buffer), mock.patch("sys.stderr", io.StringIO()):
                    code = lfs.main(
                        ["--output", str(out)],
                        runner=lab.runner,
                        topology_path=TOPOLOGY,
                    )
            self.assertEqual(code, lfs.EXIT_PASSED)
            self.assertEqual(json.loads(buffer.getvalue())["status"], "passed")
            self.assertEqual(json.loads(out.read_text("utf-8"))["exit_code"], 0)

    def test_main__returns_refusal_code_for_wrong_inventory(self) -> None:
        lab = FakeLab(containers=[])
        with mock.patch.object(
            lfs, "ReachabilityMonitor", lambda *a, **k: FakeMonitor()
        ):
            with (
                redirect_stdout(io.StringIO()),
                mock.patch("sys.stderr", io.StringIO()),
            ):
                code = lfs.main([], runner=lab.runner, topology_path=TOPOLOGY)
        self.assertEqual(code, lfs.EXIT_REFUSED)
        self.assertNotIn(lfs.INJECT_ARGV, lab.commands)


class SubprocessRunnerTests(unittest.TestCase):
    def test_missing_executable__reported_as_failed_result(self) -> None:
        result = lfs.subprocess_runner(["definitely-not-a-real-binary-xyz"])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
