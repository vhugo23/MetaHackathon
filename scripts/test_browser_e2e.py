#!/usr/bin/env python3
"""Unit tests for scripts/browser_e2e.py's pure/narrowly-isolated helpers.

Standard-library `unittest` only, matching scripts/compose_smoke.py's own
stdlib-only discipline. Covers project-name validation/generation, the
simultaneous three-port reservation scheme, runtime-environment/CORS
construction, and Compose/Vite/npm command assembly — never the real
Docker/Node/Playwright orchestration itself, which is proven by actually
running scripts/browser_e2e.py, not by a unit test.

Run directly:
    python scripts/test_browser_e2e.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import browser_e2e  # noqa: E402


class ProjectNameTests(unittest.TestCase):
    def test_generate_project_name__returns_lowercase(self) -> None:
        name = browser_e2e.generate_project_name()
        self.assertEqual(name, name.lower())

    def test_generate_project_name__matches_compose_safe_format(self) -> None:
        name = browser_e2e.generate_project_name()
        # Round-trips through validation without raising.
        self.assertEqual(browser_e2e.validate_project_name(name), name)

    def test_generate_project_name__contains_uniqueness_suffix(self) -> None:
        first = browser_e2e.generate_project_name()
        second = browser_e2e.generate_project_name()
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("meta-rne-browser-e2e-"))
        self.assertTrue(second.startswith("meta-rne-browser-e2e-"))

    def test_validate_project_name__explicit_valid_names_accepted(self) -> None:
        for name in ("meta-rne-ci", "abc123", "a", "meta_rne_browser_e2e_1"):
            with self.subTest(name=name):
                self.assertEqual(browser_e2e.validate_project_name(name), name)

    def test_validate_project_name__unsafe_or_malformed_names_rejected(self) -> None:
        invalid_names = (
            "",
            "Meta-RNE",  # uppercase
            "-leading-hyphen",
            "_leading-underscore",
            "meta rne",  # whitespace
            "meta/rne",  # path separator
            "meta;rne",  # shell metacharacter
            "meta.rne",  # dot not accepted
        )
        for name in invalid_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    browser_e2e.validate_project_name(name)


class PortReservationTests(unittest.TestCase):
    def test_reserve_ports__returns_three_distinct_live_reservations(self) -> None:
        reservations = browser_e2e.reserve_ports(3)
        try:
            self.assertEqual(len(reservations), 3)
            ports = [r.port for r in reservations]
            for port in ports:
                self.assertIsInstance(port, int)
                self.assertNotEqual(port, 0)
            self.assertEqual(len(set(ports)), 3, "ports must be pairwise distinct")
            for reservation in reservations:
                self.assertFalse(reservation.is_released)
        finally:
            for reservation in reservations:
                reservation.release()

    def test_release__closes_only_the_target_reservation(self) -> None:
        reservations = browser_e2e.reserve_ports(3)
        try:
            reservations[0].release()
            self.assertTrue(reservations[0].is_released)
            self.assertFalse(reservations[1].is_released)
            self.assertFalse(reservations[2].is_released)
        finally:
            for reservation in reservations:
                reservation.release()

    def test_release__is_idempotent(self) -> None:
        (reservation,) = browser_e2e.reserve_ports(1)
        reservation.release()
        reservation.release()  # must not raise
        self.assertTrue(reservation.is_released)


class RuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = browser_e2e.build_runtime_environment(
            db_port=15432, api_port=18080, frontend_port=14173
        )

    def test_build_runtime_environment__contains_exactly_matched_origins(self) -> None:
        self.assertEqual(self.env["META_RNE_DB_HOST_PORT"], "15432")
        self.assertEqual(self.env["META_RNE_API_HOST_PORT"], "18080")
        self.assertEqual(self.env["META_RNE_CORS_ALLOWED_ORIGINS"], "http://127.0.0.1:14173")
        self.assertEqual(self.env["VITE_API_BASE_URL"], "http://127.0.0.1:18080")
        self.assertEqual(self.env["PLAYWRIGHT_BASE_URL"], "http://127.0.0.1:14173")

    def test_build_runtime_environment__uses_127_0_0_1_never_localhost(self) -> None:
        for key in (
            "META_RNE_CORS_ALLOWED_ORIGINS",
            "VITE_API_BASE_URL",
            "PLAYWRIGHT_BASE_URL",
        ):
            with self.subTest(key=key):
                self.assertIn("127.0.0.1", self.env[key])
                self.assertNotIn("localhost", self.env[key])

    def test_build_runtime_environment__cors_origin_exactly_matches_playwright_origin(
        self,
    ) -> None:
        self.assertEqual(
            self.env["META_RNE_CORS_ALLOWED_ORIGINS"],
            self.env["PLAYWRIGHT_BASE_URL"],
        )
        self.assertFalse(self.env["META_RNE_CORS_ALLOWED_ORIGINS"].endswith("/"))


class ComposeCommandTests(unittest.TestCase):
    def test_compose_command__assembles_project_scoped_command(self) -> None:
        command = browser_e2e.compose_command(
            "meta-rne-browser-e2e-abcd1234", "up", "--build", "-d", "db", "api"
        )
        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--project-name",
                "meta-rne-browser-e2e-abcd1234",
                "up",
                "--build",
                "-d",
                "db",
                "api",
            ],
        )

    def test_compose_command__no_extra_arguments_beyond_what_was_requested(self) -> None:
        command = browser_e2e.compose_command("meta-rne-x", "down", "--volumes")
        self.assertEqual(command, ["docker", "compose", "--project-name", "meta-rne-x", "down", "--volumes"])


class CleanupQueryCommandTests(unittest.TestCase):
    def test_docker_ps_filter_command__scoped_to_project_label(self) -> None:
        command = browser_e2e.docker_ps_filter_command("meta-rne-x")
        self.assertEqual(
            command,
            ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=meta-rne-x", "--quiet"],
        )

    def test_docker_volume_filter_command__scoped_to_project_label(self) -> None:
        command = browser_e2e.docker_volume_filter_command("meta-rne-x")
        self.assertEqual(
            command,
            ["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=meta-rne-x", "--quiet"],
        )


class VitePreviewCommandTests(unittest.TestCase):
    def test_vite_preview_command__launches_vite_js_directly_through_node(self) -> None:
        frontend_dir = Path("/repo/frontend")
        command = browser_e2e.vite_preview_command(frontend_dir, 14173)

        self.assertEqual(command[0], "node")
        self.assertIn("preview", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("14173", command)
        self.assertIn("--strictPort", command)
        # The Vite entry script path must point inside node_modules/vite/bin.
        entry = Path(command[1])
        self.assertEqual(entry.name, "vite.js")
        self.assertEqual(entry.parent.name, "bin")
        self.assertEqual(entry.parent.parent.name, "vite")
        self.assertEqual(entry.parent.parent.parent.name, "node_modules")

    def test_vite_preview_command__never_invokes_npm(self) -> None:
        command = browser_e2e.vite_preview_command(Path("/repo/frontend"), 14173)
        self.assertFalse(any("npm" in str(part) for part in command))


class NpmExecutableResolutionTests(unittest.TestCase):
    def test_resolve_npm_executable__windows(self) -> None:
        self.assertEqual(browser_e2e.resolve_npm_executable("Windows"), "npm.cmd")

    def test_resolve_npm_executable__non_windows(self) -> None:
        self.assertEqual(browser_e2e.resolve_npm_executable("Linux"), "npm")
        self.assertEqual(browser_e2e.resolve_npm_executable("Darwin"), "npm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
