#!/usr/bin/env python3
"""Unit tests for scripts/demo.py's pure/narrowly-isolated helpers.

Standard-library ``unittest`` only, matching scripts/test_browser_e2e.py's
own stdlib-only discipline. Never starts real Docker, makes a real network
call, launches a browser, runs the real simulator, or sleeps in real time —
every readiness/timing dependency is injected. Proves command assembly,
project-name/run-ID/port validation, environment construction, device-ID
derivation, output formatting, and start/stop control flow (including
cleanup-on-failure) — never the real Compose/API/frontend orchestration
itself, which is proven by actually running scripts/demo.py, not by a unit
test.

Run directly:
    python scripts/test_demo.py
"""

from __future__ import annotations

import io
import socket
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo  # noqa: E402


# --------------------------------------------------------------------------
# Project name
# --------------------------------------------------------------------------


class ProjectNameTests(unittest.TestCase):
    def test_generate_project_name__is_unique_and_compose_safe(self) -> None:
        first = demo.generate_project_name()
        second = demo.generate_project_name()
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("meta-rne-demo-"))
        self.assertEqual(demo.validate_project_name(first), first)
        self.assertEqual(demo.validate_project_name(second), second)

    def test_validate_project_name__explicit_safe_name_preserved(self) -> None:
        self.assertEqual(
            demo.validate_project_name("meta-rne-demo-ci"), "meta-rne-demo-ci"
        )

    def test_validate_project_name__empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            demo.validate_project_name("")

    def test_validate_project_name__unsafe_rejected(self) -> None:
        for name in (
            "Meta-RNE",
            "-leading-hyphen",
            "meta rne",
            "meta/rne",
            "meta;rne",
            "meta.rne",
            "meta$(rm -rf)",
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    demo.validate_project_name(name)


# --------------------------------------------------------------------------
# Run ID
# --------------------------------------------------------------------------


class RunIdTests(unittest.TestCase):
    def test_generate_run_id__is_safe_and_unique(self) -> None:
        first = demo.generate_run_id()
        second = demo.generate_run_id()
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("demo-"))
        self.assertEqual(demo.validate_run_id(first), first)

    def test_validate_run_id__explicit_safe_value_preserved(self) -> None:
        self.assertEqual(demo.validate_run_id("demo-01"), "demo-01")

    def test_validate_run_id__unsafe_rejected(self) -> None:
        for value in ("", "Demo-01", "demo 01", "demo/01", "demo;01"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    demo.validate_run_id(value)


# --------------------------------------------------------------------------
# Port validation
# --------------------------------------------------------------------------


class PortValidationTests(unittest.TestCase):
    def test_validate_explicit_port__rejects_out_of_range(self) -> None:
        for value in (0, -1, 65536, 999999):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    demo.validate_explicit_port(value, label="db")

    def test_validate_explicit_port__accepts_valid_range(self) -> None:
        self.assertEqual(demo.validate_explicit_port(1, label="db"), 1)
        self.assertEqual(demo.validate_explicit_port(65535, label="db"), 65535)

    def test_reject_duplicate_ports__raises_on_overlap(self) -> None:
        with self.assertRaises(ValueError):
            demo.reject_duplicate_ports(db_port=5555, api_port=5555, frontend_port=6666)
        with self.assertRaises(ValueError):
            demo.reject_duplicate_ports(db_port=5555, api_port=6666, frontend_port=5555)
        with self.assertRaises(ValueError):
            demo.reject_duplicate_ports(db_port=5555, api_port=6666, frontend_port=6666)

    def test_reject_duplicate_ports__accepts_distinct(self) -> None:
        demo.reject_duplicate_ports(db_port=1111, api_port=2222, frontend_port=3333)

    def test_check_port_available__rejects_occupied_port(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]
        try:
            with self.assertRaises(ValueError):
                demo.check_port_available(occupied_port, label="api")
        finally:
            sock.close()

    def test_check_port_available__accepts_free_port(self) -> None:
        reservation = demo.reserve_ports(1)[0]
        port = reservation.port
        reservation.release()
        demo.check_port_available(port, label="api")


class AutomaticPortReservationTests(unittest.TestCase):
    def test_reserve_ports__returns_three_distinct_reservations(self) -> None:
        reservations = demo.reserve_ports(3)
        try:
            ports = [r.port for r in reservations]
            self.assertEqual(len(set(ports)), 3)
            for reservation in reservations:
                self.assertFalse(reservation.is_released)
        finally:
            for reservation in reservations:
                reservation.release()

    def test_reserve_ports__stay_reserved_together_until_release(self) -> None:
        reservations = demo.reserve_ports(3)
        try:
            for reservation in reservations:
                self.assertFalse(reservation.is_released)
            reservations[0].release()
            self.assertTrue(reservations[0].is_released)
            self.assertFalse(reservations[1].is_released)
            self.assertFalse(reservations[2].is_released)
        finally:
            for reservation in reservations:
                reservation.release()


# --------------------------------------------------------------------------
# Environment construction
# --------------------------------------------------------------------------


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_build_runtime_environment__contains_exact_five_overrides(self) -> None:
        env = demo.build_runtime_environment(
            db_port=15432, api_port=18080, frontend_port=15173
        )
        self.assertEqual(env["META_RNE_DB_HOST_PORT"], "15432")
        self.assertEqual(env["META_RNE_API_HOST_PORT"], "18080")
        self.assertEqual(env["META_RNE_FRONTEND_HOST_PORT"], "15173")
        self.assertEqual(env["VITE_API_BASE_URL"], "http://127.0.0.1:18080")
        self.assertEqual(env["META_RNE_CORS_ALLOWED_ORIGINS"], "http://127.0.0.1:15173")
        self.assertEqual(len(env), 5)

    def test_build_child_environment__preserves_unrelated_caller_variables(
        self,
    ) -> None:
        base_env = {"SOME_UNRELATED_VAR": "kept", "PATH": "/usr/bin"}
        child = demo.build_child_environment(
            base_env,
            demo.build_runtime_environment(db_port=1, api_port=2, frontend_port=3),
        )
        self.assertEqual(child["SOME_UNRELATED_VAR"], "kept")
        self.assertEqual(child["PATH"], "/usr/bin")
        self.assertEqual(child["META_RNE_DB_HOST_PORT"], "1")
        # The caller's own environ object must never be mutated in place.
        self.assertNotIn("META_RNE_DB_HOST_PORT", base_env)


class UrlTests(unittest.TestCase):
    def test_browser_url__uses_loopback_and_frontend_port(self) -> None:
        self.assertEqual(demo.browser_url(15173), "http://127.0.0.1:15173")

    def test_api_url__uses_loopback_and_api_port(self) -> None:
        self.assertEqual(demo.api_url(18080), "http://127.0.0.1:18080")


# --------------------------------------------------------------------------
# Command assembly
# --------------------------------------------------------------------------


class ComposeCommandTests(unittest.TestCase):
    def test_compose_up_command__exact_project_isolation_and_flags(self) -> None:
        command = demo.compose_up_command("meta-rne-demo-abcd1234", timeout_seconds=180)
        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--project-name",
                "meta-rne-demo-abcd1234",
                "up",
                "--build",
                "--detach",
                "--wait",
                "--wait-timeout",
                "180",
            ],
        )

    def test_compose_down_command__exact_project_scope_volumes_and_orphans(
        self,
    ) -> None:
        command = demo.compose_down_command("meta-rne-demo-abcd1234")
        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--project-name",
                "meta-rne-demo-abcd1234",
                "down",
                "--volumes",
                "--remove-orphans",
            ],
        )

    def test_no_generated_command_uses_prune(self) -> None:
        up = demo.compose_up_command("meta-rne-demo-x", timeout_seconds=60)
        down = demo.compose_down_command("meta-rne-demo-x")
        for command in (up, down):
            self.assertNotIn("prune", command)

    def test_no_generated_command_invokes_git(self) -> None:
        up = demo.compose_up_command("meta-rne-demo-x", timeout_seconds=60)
        down = demo.compose_down_command("meta-rne-demo-x")
        for command in (up, down):
            self.assertNotIn("git", command)


class SimulatorCommandTests(unittest.TestCase):
    def test_simulator_command__uses_sys_executable_and_exact_arguments(self) -> None:
        command = demo.simulator_command(api_port=18080, run_id="demo-01")
        self.assertEqual(command[0], sys.executable)
        self.assertIn(
            str(demo.REPO_ROOT / "scripts" / "telemetry_simulator.py"), command
        )
        self.assertIn("--base-url", command)
        self.assertIn("http://127.0.0.1:18080", command)
        self.assertIn("--scenario", command)
        self.assertIn("all-anomalies", command)
        self.assertIn("--run-id", command)
        self.assertIn("demo-01", command)


class DeviceIdTests(unittest.TestCase):
    def test_derive_device_id__matches_simulator_convention_exactly(self) -> None:
        self.assertEqual(demo.derive_device_id("demo-01"), "sim-demo-01-all-anomalies")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


class SuccessOutputTests(unittest.TestCase):
    def test_print_success_summary__contains_required_fields(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            demo.print_success_summary(
                project_name="meta-rne-demo-abcd1234",
                api_port=18080,
                frontend_port=15173,
                device_id="sim-demo-01-all-anomalies",
            )
        output = buffer.getvalue()
        self.assertIn("meta-rne-demo-abcd1234", output)
        self.assertIn("http://127.0.0.1:15173", output)
        self.assertIn("http://127.0.0.1:18080", output)
        self.assertIn("sim-demo-01-all-anomalies", output)
        self.assertIn("Telemetry device", output)
        self.assertIn(
            "python scripts/demo.py stop --project-name meta-rne-demo-abcd1234", output
        )


# --------------------------------------------------------------------------
# HTTP readiness (fully injectable — no real sockets, no real sleeping)
# --------------------------------------------------------------------------


class ReadinessTests(unittest.TestCase):
    def test_wait_for_api_ready__accepts_expected_health_body(self) -> None:
        calls: list[str] = []

        def fake_get(url: str, timeout: float) -> tuple[int, bytes]:
            calls.append(url)
            return 200, b'{"status": "ok"}'

        demo.wait_for_api_ready(
            18080,
            deadline_at=1.0,
            http_get=fake_get,
            now_fn=lambda: 0.0,
            sleep_fn=lambda seconds: None,
        )
        self.assertEqual(calls, ["http://127.0.0.1:18080/health"])

    def test_wait_for_api_ready__rejects_wrong_body_then_times_out(self) -> None:
        def fake_get(url: str, timeout: float) -> tuple[int, bytes]:
            return 200, b"not the expected liveness body"

        times = iter([0.0, 0.5, 1.5])

        with self.assertRaises(demo.DemoError):
            demo.wait_for_api_ready(
                18080,
                deadline_at=1.0,
                http_get=fake_get,
                now_fn=lambda: next(times),
                sleep_fn=lambda seconds: None,
            )

    def test_wait_for_api_ready__retries_transient_connection_failure(self) -> None:
        attempts = {"count": 0}

        def fake_get(url: str, timeout: float) -> tuple[int, bytes]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise ConnectionRefusedError("not up yet")
            return 200, b'{"status": "ok"}'

        times = iter([0.0, 0.2, 0.4])
        demo.wait_for_api_ready(
            18080,
            deadline_at=1.0,
            http_get=fake_get,
            now_fn=lambda: next(times),
            sleep_fn=lambda seconds: None,
        )
        self.assertEqual(attempts["count"], 2)

    def test_wait_for_frontend_ready__requires_non_empty_html_body(self) -> None:
        def fake_get(url: str, timeout: float) -> tuple[int, bytes]:
            return 200, b"<!doctype html><html></html>"

        demo.wait_for_frontend_ready(
            15173,
            deadline_at=1.0,
            http_get=fake_get,
            now_fn=lambda: 0.0,
            sleep_fn=lambda seconds: None,
        )

    def test_wait_for_frontend_ready__rejects_empty_body(self) -> None:
        def fake_get(url: str, timeout: float) -> tuple[int, bytes]:
            return 200, b""

        times = iter([0.0, 0.5, 1.5])
        with self.assertRaises(demo.DemoError):
            demo.wait_for_frontend_ready(
                15173,
                deadline_at=1.0,
                http_get=fake_get,
                now_fn=lambda: next(times),
                sleep_fn=lambda seconds: None,
            )


# --------------------------------------------------------------------------
# Start orchestration (subprocess/socket layer fully mocked)
# --------------------------------------------------------------------------


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class StartOrchestrationTests(unittest.TestCase):
    def _config(self) -> "demo.DemoConfig":
        return demo.DemoConfig(
            project_name="meta-rne-demo-abcd1234",
            db_port=15432,
            api_port=18080,
            frontend_port=15173,
            run_id="demo-01",
            timeout_seconds=120.0,
        )

    def test_successful_start__does_not_run_cleanup(self) -> None:
        run_calls: list[list[str]] = []

        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            run_calls.append(command)
            return FakeCompletedProcess(0)

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            mock.patch.object(demo, "wait_for_api_ready"),
            mock.patch.object(demo, "wait_for_frontend_ready"),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertEqual(exit_code, 0)
        self.assertFalse(
            any(
                c[:3] == ["docker", "compose", "--project-name"] and "down" in c
                for c in run_calls
            )
        )

    def test_compose_startup_failure__triggers_exact_project_cleanup(self) -> None:
        cleanup_calls: list[str] = []

        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            if "up" in command:
                raise demo.DemoError("compose up failed")
            return FakeCompletedProcess(0)

        def fake_cleanup(project_name: str, timeout_seconds: float) -> None:
            cleanup_calls.append(project_name)

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            mock.patch.object(demo, "clean_project", side_effect=fake_cleanup),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(cleanup_calls, ["meta-rne-demo-abcd1234"])

    def test_api_readiness_failure__triggers_cleanup(self) -> None:
        cleanup_calls: list[str] = []

        with (
            mock.patch.object(
                demo, "run_checked", return_value=FakeCompletedProcess(0)
            ),
            mock.patch.object(
                demo,
                "wait_for_api_ready",
                side_effect=demo.DemoError("api never became ready"),
            ),
            mock.patch.object(
                demo, "clean_project", side_effect=lambda p, t: cleanup_calls.append(p)
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(cleanup_calls, ["meta-rne-demo-abcd1234"])

    def test_frontend_readiness_failure__triggers_cleanup(self) -> None:
        cleanup_calls: list[str] = []

        with (
            mock.patch.object(
                demo, "run_checked", return_value=FakeCompletedProcess(0)
            ),
            mock.patch.object(demo, "wait_for_api_ready"),
            mock.patch.object(
                demo,
                "wait_for_frontend_ready",
                side_effect=demo.DemoError("frontend never became ready"),
            ),
            mock.patch.object(
                demo, "clean_project", side_effect=lambda p, t: cleanup_calls.append(p)
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(cleanup_calls, ["meta-rne-demo-abcd1234"])

    def test_simulator_failure__triggers_cleanup(self) -> None:
        cleanup_calls: list[str] = []

        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            if "telemetry_simulator.py" in " ".join(command):
                raise demo.DemoError("simulator failed")
            return FakeCompletedProcess(0)

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            mock.patch.object(demo, "wait_for_api_ready"),
            mock.patch.object(demo, "wait_for_frontend_ready"),
            mock.patch.object(
                demo, "clean_project", side_effect=lambda p, t: cleanup_calls.append(p)
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(cleanup_calls, ["meta-rne-demo-abcd1234"])

    def test_keyboard_interrupt_during_startup__triggers_cleanup(self) -> None:
        cleanup_calls: list[str] = []

        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            raise KeyboardInterrupt()

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            mock.patch.object(
                demo, "clean_project", side_effect=lambda p, t: cleanup_calls.append(p)
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(cleanup_calls, ["meta-rne-demo-abcd1234"])

    def test_cleanup_failure__does_not_replace_original_failure(self) -> None:
        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            if "up" in command:
                raise demo.DemoError("original compose up failure")
            return FakeCompletedProcess(0)

        def failing_cleanup(project_name: str, timeout_seconds: float) -> None:
            raise demo.DemoError("cleanup itself failed")

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            mock.patch.object(demo, "clean_project", side_effect=failing_cleanup),
            redirect_stdout(io.StringIO()),
            redirect_stderr(captured := io.StringIO()),
        ):
            exit_code = demo.start(self._config())

        self.assertNotEqual(exit_code, 0)
        self.assertIn("original compose up failure", captured.getvalue())


# --------------------------------------------------------------------------
# Stop orchestration
# --------------------------------------------------------------------------


class StopOrchestrationTests(unittest.TestCase):
    def test_stop__uses_exact_project_scoped_down_command(self) -> None:
        recorded: list[list[str]] = []

        def fake_run_checked(
            command: list[str], **kwargs: object
        ) -> FakeCompletedProcess:
            recorded.append(command)
            return FakeCompletedProcess(0)

        with (
            mock.patch.object(demo, "run_checked", side_effect=fake_run_checked),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = demo.stop("meta-rne-demo-abcd1234")

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(
            recorded[0],
            [
                "docker",
                "compose",
                "--project-name",
                "meta-rne-demo-abcd1234",
                "down",
                "--volumes",
                "--remove-orphans",
            ],
        )

    def test_stop__unsafe_project_name_invokes_no_subprocess(self) -> None:
        with mock.patch.object(demo, "run_checked") as run_checked:
            exit_code = demo.stop("../not safe")
        run_checked.assert_not_called()
        self.assertNotEqual(exit_code, 0)

    def test_stop__prints_confirmation_naming_the_exact_project(self) -> None:
        with (
            mock.patch.object(
                demo, "run_checked", return_value=FakeCompletedProcess(0)
            ),
            redirect_stdout(buffer := io.StringIO()),
        ):
            demo.stop("meta-rne-demo-abcd1234")
        self.assertIn("meta-rne-demo-abcd1234", buffer.getvalue())


# --------------------------------------------------------------------------
# main() and CLI surface
# --------------------------------------------------------------------------


class MainTests(unittest.TestCase):
    def test_main__returns_zero_on_successful_start(self) -> None:
        with (
            mock.patch.object(demo, "start", return_value=0) as fake_start,
            redirect_stdout(io.StringIO()),
        ):
            exit_code = demo.main(["start", "--project-name", "meta-rne-demo-fixed"])
        self.assertEqual(exit_code, 0)
        fake_start.assert_called_once()

    def test_main__returns_nonzero_on_failed_start(self) -> None:
        with (
            mock.patch.object(demo, "start", return_value=1),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = demo.main(["start", "--project-name", "meta-rne-demo-fixed"])
        self.assertEqual(exit_code, 1)

    def test_main__stop_delegates_to_stop_with_project_name(self) -> None:
        with (
            mock.patch.object(demo, "stop", return_value=0) as fake_stop,
            redirect_stdout(io.StringIO()),
        ):
            exit_code = demo.main(["stop", "--project-name", "meta-rne-demo-fixed"])
        self.assertEqual(exit_code, 0)
        fake_stop.assert_called_once_with("meta-rne-demo-fixed")

    def test_help__documents_both_commands_and_permitted_options(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit):
            demo.main(["--help"])
        top_level_help = buffer.getvalue()
        self.assertIn("start", top_level_help)
        self.assertIn("stop", top_level_help)

        start_buffer = io.StringIO()
        with redirect_stdout(start_buffer), self.assertRaises(SystemExit):
            demo.main(["start", "--help"])
        start_help = start_buffer.getvalue()
        for option in (
            "--project-name",
            "--db-port",
            "--api-port",
            "--frontend-port",
            "--run-id",
            "--timeout-seconds",
        ):
            self.assertIn(option, start_help)

        stop_buffer = io.StringIO()
        with redirect_stdout(stop_buffer), self.assertRaises(SystemExit):
            demo.main(["stop", "--help"])
        stop_help = stop_buffer.getvalue()
        self.assertIn("--project-name", stop_help)

    def test_no_browser_launch_function_is_called(self) -> None:
        # Structural guard: the module must not define/import a browser
        # launcher (e.g. webbrowser.open) anywhere in its public surface.
        self.assertFalse(hasattr(demo, "webbrowser"))

    def test_no_state_file_is_written(self) -> None:
        # Structural guard: the module must not define a state-file path
        # constant or writer function.
        self.assertFalse(hasattr(demo, "STATE_FILE"))
        self.assertFalse(hasattr(demo, "write_state_file"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
