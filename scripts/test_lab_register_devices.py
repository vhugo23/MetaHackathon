#!/usr/bin/env python3
"""Unit tests for scripts/lab_register_devices.py (NPE-1C3B1).

Standard-library ``unittest`` only. No database, Docker, Containerlab or lab
is needed: the CLI is driven with an in-memory unit of work. The backend
package must be importable (the script adds backend/src to ``sys.path``).

Run directly:
    python scripts/test_lab_register_devices.py
"""

from __future__ import annotations

import ast
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_register_devices as lrd  # noqa: E402

from meta_rne.application.lab_device_registration import LAB1_ROUTER_DEVICE_IDS  # noqa: E402
from meta_rne.domain.config import VendorType  # noqa: E402
from meta_rne.domain.device import Device  # noqa: E402
from meta_rne.persistence.memory.store import InMemoryStore  # noqa: E402
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _run(
    store: InMemoryStore, argv: list[str] | None = None, environ: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = lrd.main(
            argv or [],
            environ={} if environ is None else environ,
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            clock=lambda: NOW,
        )
    return code, out.getvalue(), err.getvalue()


class RegisterDevicesCliTests(unittest.TestCase):
    def test_registers_the_four_fixed_routers(self) -> None:
        store = InMemoryStore()
        code, out, _ = _run(store)

        self.assertEqual(code, lrd.EXIT_OK)
        self.assertEqual(set(store.devices), set(LAB1_ROUTER_DEVICE_IDS))
        for device_id in LAB1_ROUTER_DEVICE_IDS:
            self.assertIn(f"registered {device_id}", out)
            self.assertIs(store.devices[device_id].vendor, VendorType.FRR)

    def test_second_run_is_idempotent(self) -> None:
        store = InMemoryStore()
        _run(store)
        code, out, _ = _run(store)

        self.assertEqual(code, lrd.EXIT_OK)
        self.assertEqual(out.count("already registered"), 4)

    def test_conflicting_device_exits_1_and_changes_nothing(self) -> None:
        store = InMemoryStore()
        store.devices["lab1-leaf-1"] = Device(
            device_id="lab1-leaf-1",
            vendor=VendorType.ARISTA_EOS,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
        code, _, err = _run(store)

        self.assertEqual(code, lrd.EXIT_CONFLICT)
        self.assertIn("conflict", err)
        self.assertEqual(set(store.devices), {"lab1-leaf-1"})

    def test_any_argument_is_rejected(self) -> None:
        store = InMemoryStore()
        for argv in (["--device", "x"], ["lab1-leaf-1"], ["--vendor", "frr"]):
            code, _, _ = _run(store, argv)
            self.assertEqual(code, lrd.EXIT_ERROR)
        self.assertEqual(store.devices, {})

    def test_missing_database_url_without_injected_factory_exits_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = lrd.main([], environ={})

        self.assertEqual(code, lrd.EXIT_ERROR)
        self.assertIn("DATABASE_URL", err.getvalue())

    def test_cli_imports_no_subprocess_network_or_lab_modules(self) -> None:
        tree = ast.parse(Path(lrd.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for forbidden in ("subprocess", "socket", "urllib", "http", "requests", "httpx"):
            self.assertNotIn(forbidden, imported)
        self.assertFalse([name for name in imported if name.startswith("lab_")])
        self.assertFalse(
            [name for name in imported if name.startswith("meta_rne.adapters.containerlab_frr")]
        )


if __name__ == "__main__":
    unittest.main()
