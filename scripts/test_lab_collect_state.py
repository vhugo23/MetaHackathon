#!/usr/bin/env python3
"""Unit tests for scripts/lab_collect_state.py (NPE-1C3A).

Standard-library ``unittest`` only. No Docker, Containerlab, WSL, or running
lab is needed: commands are answered from the captured fixtures under
backend/tests/fixtures/containerlab_frr. The backend package must be
importable (the script adds backend/src to ``sys.path`` itself).

Run directly:
    python scripts/test_lab_collect_state.py
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_collect_state as lcs  # noqa: E402
import lab_failure_scenario  # noqa: E402
import lab_validate  # noqa: E402
from meta_rne.adapters.containerlab_frr import (  # noqa: E402
    CommandNotAllowedError,
    CommandResult,
)
from meta_rne.adapters.containerlab_frr import collector as collector_module  # noqa: E402

FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "tests"
    / "fixtures"
    / "containerlab_frr"
)
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

_FILE_FOR_COMMAND = {
    ("ip", "-j", "addr"): "ip_j_addr.json",
    ("vtysh", "-c", "show bgp summary json"): "show_bgp_summary.json",
    ("vtysh", "-c", "show ip route json"): "show_ip_route.json",
    ("ip", "-o", "link", "show"): "ip_o_link.txt",
    ("ip", "-o", "addr", "show"): "ip_o_addr.txt",
}


def fixture_runner(
    fixture_set: str, fail: set[tuple[str, tuple[str, ...]]] | None = None
):  # type: ignore[no-untyped-def]
    failing = fail or set()

    def run(container: str, command: Sequence[str]) -> CommandResult:
        command = tuple(command)
        if (container, command) in failing:
            return CommandResult(1, "", "simulated failure")
        node = container.removeprefix(collector_module.CONTAINER_PREFIX)
        name = (
            f"ping_{command[-1]}.txt"
            if command[0] == "ping"
            else _FILE_FOR_COMMAND[command]
        )
        return CommandResult(
            0, (FIXTURES / fixture_set / node / name).read_text(encoding="utf-8")
        )

    return run


def run_main(
    argv: list[str],
    runner,  # type: ignore[no-untyped-def]
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = lcs.main(
            argv,
            runner=runner,
            clock=lambda: NOW,
            collection_id_factory=lambda: "run-1",
        )
    return code, out.getvalue(), err.getvalue()


class InventoryStaysInSyncWithLabScripts(unittest.TestCase):
    def test_nodes_and_lab_name_match_lab_validate(self) -> None:
        self.assertEqual(collector_module.ROUTER_NODES, lab_validate.ROUTERS)
        self.assertEqual(collector_module.HOST_NODES, lab_validate.HOSTS)
        self.assertEqual(collector_module.LAB_NAME, lab_validate.LAB_NAME)

    def test_containers_match_failure_harness_inventory(self) -> None:
        self.assertEqual(
            collector_module.lab_containers(), lab_failure_scenario.EXPECTED_CONTAINERS
        )
        self.assertEqual(
            collector_module.CONTAINER_PREFIX, lab_failure_scenario.NODE_PREFIX
        )

    def test_ping_targets_match_failure_harness(self) -> None:
        self.assertEqual(
            collector_module.HOST_PING_TARGETS, dict(lab_failure_scenario.HOST_PINGS)
        )


class DockerExecArgv(unittest.TestCase):
    def test_linux_runs_docker_directly(self) -> None:
        argv = lcs.docker_exec_argv(
            "clab-meta-rne-bgp-leaf-1", ("ip", "-j", "addr"), platform="linux"
        )
        self.assertEqual(
            argv, ["docker", "exec", "clab-meta-rne-bgp-leaf-1", "ip", "-j", "addr"]
        )

    def test_windows_goes_through_wsl_never_a_windows_docker(self) -> None:
        argv = lcs.docker_exec_argv(
            "clab-meta-rne-bgp-leaf-1",
            ("vtysh", "-c", "show bgp summary json"),
            platform="win32",
        )
        self.assertEqual(argv[:6], ["wsl", "-d", "Ubuntu", "--exec", "docker", "exec"])
        self.assertEqual(argv[-3:], ["vtysh", "-c", "show bgp summary json"])

    def test_disallowed_command_never_produces_argv(self) -> None:
        for container, command in (
            ("clab-meta-rne-bgp-leaf-1", ("ip", "link", "set", "eth1", "down")),
            ("clab-meta-rne-bgp-leaf-1", ("vtysh", "-c", "configure terminal")),
            ("clab-other-leaf-1", ("ip", "-j", "addr")),
            ("clab-meta-rne-bgp-host-1", ("ping", "-c", "3", "-W", "2", "8.8.8.8")),
        ):
            with self.subTest(container=container, command=command):
                with self.assertRaises(CommandNotAllowedError):
                    lcs.docker_exec_argv(container, command)


class SubprocessRunner(unittest.TestCase):
    def test_disallowed_command_is_rejected_before_any_process_starts(self) -> None:
        with mock.patch.object(subprocess, "run") as run:
            with self.assertRaises(CommandNotAllowedError):
                lcs.subprocess_runner(
                    "clab-meta-rne-bgp-leaf-1", ("ip", "link", "set", "eth1", "down")
                )
            run.assert_not_called()

    def test_allowed_command_runs_once_with_timeout_and_no_shell(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="out", stderr="err")
        with mock.patch.object(subprocess, "run", return_value=completed) as run:
            result = lcs.subprocess_runner(
                "clab-meta-rne-bgp-leaf-1", ("ip", "-j", "addr")
            )
        self.assertEqual(result, CommandResult(0, "out", "err"))
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["timeout"], lcs.COMMAND_TIMEOUT_S)
        self.assertNotIn("shell", kwargs)
        self.assertIsInstance(run.call_args.args[0], list)

    def test_timeout_and_missing_executable_become_failed_results(self) -> None:
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)
        ):
            result = lcs.subprocess_runner(
                "clab-meta-rne-bgp-leaf-1", ("ip", "-j", "addr")
            )
        self.assertEqual(result.returncode, 124)
        with mock.patch.object(
            subprocess, "run", side_effect=FileNotFoundError("docker")
        ):
            result = lcs.subprocess_runner(
                "clab-meta-rne-bgp-leaf-1", ("ip", "-j", "addr")
            )
        self.assertEqual(result.returncode, 127)


class Main(unittest.TestCase):
    def test_baseline_emits_normalized_json_for_six_nodes(self) -> None:
        code, out, _ = run_main([], fixture_runner("baseline"))
        self.assertEqual(code, lcs.EXIT_COLLECTED)
        document = json.loads(out)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["collection_id"], "run-1")
        self.assertEqual(document["collected_at"], "2026-10-07T12:00:00Z")
        self.assertEqual(
            [node["node_id"] for node in document["nodes"]],
            ["spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2"],
        )
        leaf1 = next(n for n in document["nodes"] if n["node_id"] == "leaf-1")
        self.assertEqual(leaf1["role"], "router")
        self.assertEqual(leaf1["bgp"]["local_as"], 65101)
        self.assertEqual(
            [n["state"] for n in leaf1["bgp"]["neighbors"]], ["Established"] * 2
        )
        route = next(r for r in leaf1["routes"] if r["prefix"] == "10.1.2.0/24")
        self.assertEqual(route["ecmp_path_count"], 2)
        self.assertEqual(
            {(h["ip"], h["interface"]) for h in route["next_hops"]},
            {("10.255.0.0", "eth1"), ("10.255.0.2", "eth2")},
        )
        self.assertEqual(leaf1["source"]["collection_id"], "run-1")
        host1 = next(n for n in document["nodes"] if n["node_id"] == "host-1")
        self.assertIsNone(host1["bgp"])
        self.assertEqual(
            host1["reachability"][0],
            {"from_node": "host-1", "target": "10.1.2.10", "sent": 3, "received": 3},
        )

    def test_observed_degraded_state_is_not_an_error(self) -> None:
        code, out, err = run_main([], fixture_runner("degraded-active"))
        self.assertEqual(code, lcs.EXIT_COLLECTED)
        self.assertEqual(err, "")
        leaf1 = next(n for n in json.loads(out)["nodes"] if n["node_id"] == "leaf-1")
        states = {n["neighbor_ip"]: n["state"] for n in leaf1["bgp"]["neighbors"]}
        self.assertEqual(states, {"10.255.0.0": "Active", "10.255.0.2": "Established"})
        eth1 = next(i for i in leaf1["interfaces"] if i["name"] == "eth1")
        self.assertEqual(eth1["oper_state"], "down")

    def test_unavailable_facet_exits_1_and_is_not_reported_as_down(self) -> None:
        bgp = ("vtysh", "-c", "show bgp summary json")
        runner = fixture_runner("baseline", {("clab-meta-rne-bgp-leaf-1", bgp)})
        code, out, err = run_main([], runner)
        self.assertEqual(code, lcs.EXIT_INCOMPLETE)
        self.assertIn("collection incomplete", err)
        leaf1 = next(n for n in json.loads(out)["nodes"] if n["node_id"] == "leaf-1")
        self.assertIsNone(leaf1["bgp"])
        self.assertEqual([u["facet"] for u in leaf1["unavailable"]], ["bgp"])
        self.assertNotIn("Idle", out)
        self.assertNotIn("Active", out)

    def test_output_file_matches_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            code, out, _ = run_main(["--output", str(path)], fixture_runner("baseline"))
            self.assertEqual(code, lcs.EXIT_COLLECTED)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), json.loads(out)
            )

    def test_internal_error_exits_2_without_traceback(self) -> None:
        def broken(container: str, command: Sequence[str]) -> CommandResult:
            raise RuntimeError("boom")

        code, out, err = run_main([], broken)
        self.assertEqual(code, lcs.EXIT_INTERNAL_ERROR)
        self.assertEqual(out, "")
        self.assertIn("boom", err)

    def test_runner_is_never_asked_for_a_disallowed_command(self) -> None:
        seen: list[tuple[str, tuple[str, ...]]] = []
        inner = fixture_runner("baseline")

        def recording(container: str, command: Sequence[str]) -> CommandResult:
            seen.append((container, tuple(command)))
            return inner(container, command)

        run_main([], recording)
        self.assertEqual(len(seen), 18)
        for container, command in seen:
            collector_module.assert_command_allowed(container, command)

    def test_cli_has_only_an_output_option(self) -> None:
        options = {
            option
            for action in lcs.build_parser()._actions
            for option in action.option_strings
        }
        self.assertEqual(options, {"-h", "--help", "--output"})


class ReadOnlyByConstruction(unittest.TestCase):
    """The collector CLI may not reach the platform, the database, or the
    lab's write paths."""

    def _imports(self, path: Path) -> set[str]:
        imported: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_cli_imports_no_network_http_or_persistence_modules(self) -> None:
        imported = self._imports(Path(lcs.__file__))
        for forbidden in (
            "urllib",
            "urllib.request",
            "http",
            "http.client",
            "socket",
            "sqlalchemy",
            "fastapi",
            "requests",
            "httpx",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertFalse(
            [
                name
                for name in imported
                if name.startswith(
                    ("meta_rne.persistence", "meta_rne.api", "meta_rne.application")
                )
            ]
        )

    def test_cli_does_not_import_the_fault_injector(self) -> None:
        self.assertNotIn("lab_failure_scenario", self._imports(Path(lcs.__file__)))


if __name__ == "__main__":
    unittest.main()
