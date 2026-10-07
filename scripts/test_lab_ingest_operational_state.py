#!/usr/bin/env python3
"""Unit tests for scripts/lab_ingest_operational_state.py (NPE-1C3B3).

Standard-library ``unittest`` only. No Docker, Containerlab, WSL, database or
running lab is needed: commands are answered from the captured fixtures and
the unit of work is in-memory. The backend package must be importable (the
script adds backend/src to ``sys.path`` itself).

Run directly:
    python scripts/test_lab_ingest_operational_state.py
"""

from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_ingest_operational_state as lios  # noqa: E402
from test_lab_collect_state import fixture_runner  # noqa: E402

from meta_rne.adapters.containerlab_frr import CommandResult  # noqa: E402
from meta_rne.application.lab_device_registration import LAB1_ROUTER_DEVICE_IDS  # noqa: E402
from meta_rne.domain.config import VendorType  # noqa: E402
from meta_rne.domain.device import Device  # noqa: E402
from meta_rne.persistence.memory.store import InMemoryStore  # noqa: E402
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _registered_store(
    device_ids: tuple[str, ...] = LAB1_ROUTER_DEVICE_IDS,
) -> InMemoryStore:
    store = InMemoryStore()
    for device_id in device_ids:
        store.devices[device_id] = Device(
            device_id=device_id,
            vendor=VendorType.FRR,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
    return store


def _run(
    store: InMemoryStore,
    runner: Any,
    argv: list[str] | None = None,
    now: datetime = NOW,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = lios.main(
            argv or [],
            environ={},
            runner=runner,
            clock=lambda: now,
            collection_id_factory=lambda: "run-1",
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        )
    return code, out.getvalue(), err.getvalue()


class Main(unittest.TestCase):
    def test_baseline_prints_zero_anomalies_and_creates_no_incident(self) -> None:
        store = _registered_store()
        code, out, _ = _run(store, fixture_runner("baseline"))

        self.assertEqual(code, lios.EXIT_INGESTED)
        document = json.loads(out)
        self.assertEqual(document["anomaly_count"], 0)
        self.assertEqual(document["incidents_created"], 0)
        self.assertEqual(document["incidents_updated"], 0)
        self.assertTrue(document["complete"])
        self.assertEqual(store.incidents, {})

    def test_degraded_fixture_creates_the_two_expected_incidents(self) -> None:
        store = _registered_store()
        code, out, err = _run(store, fixture_runner("degraded-active"))

        self.assertEqual(code, lios.EXIT_INGESTED)
        document = json.loads(out)
        self.assertEqual(document["anomaly_count"], 2)
        self.assertEqual(document["incidents_created"], 2)
        self.assertEqual(
            sorted(
                (i["device_id"], i["affected_resource"]) for i in document["incidents"]
            ),
            [
                ("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
                ("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
            ],
        )
        for anomaly in document["anomalies"]:
            self.assertIsNone(anomaly["previous_state"])
        self.assertEqual(len(store.incidents), 2)
        # Structured incident events go to stderr, never into the JSON on stdout.
        self.assertEqual(len(err.strip().splitlines()), 2)

    def test_repeat_ingestion_updates_rather_than_duplicates(self) -> None:
        store = _registered_store()
        _run(store, fixture_runner("degraded-active"))
        code, out, _ = _run(
            store, fixture_runner("degraded-active"), now=NOW + timedelta(seconds=30)
        )

        document = json.loads(out)
        self.assertEqual(code, lios.EXIT_INGESTED)
        self.assertEqual(
            (document["incidents_created"], document["incidents_updated"]), (0, 2)
        )
        self.assertEqual(len(store.incidents), 2)

    def test_recovered_ingestion_after_degraded_does_not_resolve(self) -> None:
        store = _registered_store()
        _run(store, fixture_runner("degraded-active"))
        code, out, _ = _run(
            store, fixture_runner("baseline"), now=NOW + timedelta(seconds=30)
        )

        document = json.loads(out)
        self.assertEqual(code, lios.EXIT_INGESTED)
        self.assertEqual(document["anomaly_count"], 0)
        self.assertEqual(len(store.incidents), 2)
        self.assertTrue(all(i.status.value == "OPEN" for i in store.incidents.values()))

    def test_output_is_deterministic(self) -> None:
        first = _run(_registered_store(), fixture_runner("degraded-active"))[1]
        second = _run(_registered_store(), fixture_runner("degraded-active"))[1]

        first_document, second_document = json.loads(first), json.loads(second)
        for document in (first_document, second_document):
            for incident in document["incidents"]:
                incident.pop("incident_id")  # generated, not deterministic
        self.assertEqual(first_document, second_document)

    def test_unregistered_device_exits_3_and_persists_nothing(self) -> None:
        store = _registered_store(("lab1-leaf-1", "lab1-spine-2", "lab1-leaf-2"))
        code, out, err = _run(store, fixture_runner("degraded-active"))

        self.assertEqual(code, lios.EXIT_DEVICE_NOT_REGISTERED)
        self.assertEqual(out, "")
        self.assertIn("lab_register_devices.py", err)
        self.assertEqual(store.incidents, {})

    def test_unavailable_facet_exits_1_and_creates_no_incident(self) -> None:
        store = _registered_store()
        failing = {
            (f"clab-meta-rne-bgp-{node}", ("vtysh", "-c", "show bgp summary json"))
            for node in ("leaf-1", "spine-1")
        }
        code, out, err = _run(store, fixture_runner("degraded-active", fail=failing))

        self.assertEqual(code, lios.EXIT_INCOMPLETE)
        self.assertEqual(json.loads(out)["anomaly_count"], 0)
        self.assertFalse(json.loads(out)["complete"])
        self.assertIn("incomplete", err)
        self.assertEqual(store.incidents, {})

    def test_collector_failure_exits_2_without_traceback(self) -> None:
        def broken(container: str, command: Any) -> CommandResult:
            raise RuntimeError("docker exploded")

        code, out, err = _run(_registered_store(), broken)

        self.assertEqual(code, lios.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("internal error", err)
        self.assertNotIn("Traceback", err)

    def test_missing_database_url_exits_2_before_collecting(self) -> None:
        calls: list[object] = []

        def runner(container: str, command: Any) -> CommandResult:
            calls.append((container, command))
            return CommandResult(0, "", "")

        err = io.StringIO()
        with redirect_stderr(err):
            code = lios.main([], environ={}, runner=runner)

        self.assertEqual(code, lios.EXIT_ERROR)
        self.assertIn("DATABASE_URL", err.getvalue())
        self.assertEqual(calls, [])

    def test_database_failure_exits_2(self) -> None:
        def broken_factory() -> InMemoryUnitOfWork:
            raise RuntimeError("database unreachable")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = lios.main(
                [],
                environ={},
                runner=fixture_runner("baseline"),
                clock=lambda: NOW,
                collection_id_factory=lambda: "run-1",
                unit_of_work_factory=broken_factory,
            )

        self.assertEqual(code, lios.EXIT_ERROR)
        self.assertIn("database unreachable", err.getvalue())

    def test_any_argument_is_rejected_before_collecting(self) -> None:
        calls: list[object] = []

        def runner(container: str, command: Any) -> CommandResult:
            calls.append((container, command))
            return CommandResult(0, "", "")

        store = _registered_store()
        for argv in (
            ["--node", "leaf-1"],
            ["--device", "lab1-leaf-1"],
            ["--command", "ip link set eth1 down"],
            ["--neighbor", "10.255.0.0"],
            ["lab1-leaf-1"],
        ):
            code, out, _ = _run(store, runner, argv)
            self.assertEqual(code, lios.EXIT_ERROR)
            self.assertEqual(out, "")
        self.assertEqual(calls, [])
        self.assertEqual(store.incidents, {})


class CompositionIsFixedAndControlled(unittest.TestCase):
    def _imports(self) -> set[str]:
        imported: set[str] = set()
        tree = ast.parse(Path(lios.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        return imported

    def test_collector_is_the_existing_fixed_one(self) -> None:
        self.assertIs(
            lios.lab_collect_state.subprocess_runner.__module__, "lab_collect_state"
        )
        self.assertIn("lab_collect_state", self._imports())

    def test_cli_has_no_subprocess_http_or_fault_injection_imports(self) -> None:
        imported = self._imports()
        for forbidden in (
            "subprocess",
            "socket",
            "urllib",
            "urllib.request",
            "http",
            "http.client",
            "requests",
            "httpx",
            "fastapi",
            "lab_failure_scenario",
            "lab_validate",
            "lab_register_devices",
        ):
            self.assertNotIn(forbidden, imported)

    def test_cli_uses_no_resolution_or_device_creation_apis(self) -> None:
        source = Path(lios.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "ResolveIncidentService",
            "incidents.resolve",
            "RegisterLabDevicesService",
            "devices.save",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
