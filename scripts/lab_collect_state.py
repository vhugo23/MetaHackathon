#!/usr/bin/env python3
"""Read-only live operational-state collector for Lab 1 (NPE-1C3A, ADR-0003).

Collects one fabric snapshot of the already-deployed Lab 1 and prints it as
normalized JSON (optionally also writing it to a file):

    python scripts/lab_collect_state.py [--output state.json]

It only observes. It runs a fixed allowlist of read-only commands inside the
six fixed Lab 1 containers and does nothing else: it does not POST to the
platform, translate into telemetry, register devices, write database records,
modify the lab, or inject faults. There is no option that accepts a container,
command, interface, or address.

The parsing and normalization live in ``meta_rne.adapters.containerlab_frr``
(which never spawns a process). This script is the outermost boundary: it
owns the one real subprocess runner, which re-checks every call against the
same allowlist before executing it.

Docker access. The lab runs on the native Docker Engine inside Ubuntu WSL, and
the backend needs Python >= 3.12 (the WSL distro has 3.10). On Windows the
runner therefore reaches the native engine with ``wsl -d Ubuntu --exec docker
...``; it never uses a Windows-side ``docker`` (Docker Desktop is not used for
the lab). On Linux it runs ``docker`` directly.

Exit codes:
    0  collected; every facet on every node was observed
    1  collected, but at least one facet was unavailable (see "unavailable")
    2  unexpected internal error

An *observed* failure (a down interface, an Active/Idle BGP neighbor) is not an
error and exits 0: it is the network's state, reported faithfully.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from meta_rne.adapters.containerlab_frr import (  # noqa: E402
    CommandResult,
    ContainerlabFrrCollector,
    NodeCommandRunner,
)
from meta_rne.adapters.containerlab_frr.collector import assert_command_allowed  # noqa: E402
from meta_rne.domain.operational_state import (  # noqa: E402
    FabricOperationalState,
    RouteObservation,
)

EXIT_COLLECTED = 0
EXIT_INCOMPLETE = 1
EXIT_INTERNAL_ERROR = 2

SCHEMA_VERSION = 1
COMMAND_TIMEOUT_S = 20.0
WSL_DISTRO = "Ubuntu"
_WINDOWS_DOCKER_PREFIX: tuple[str, ...] = ("wsl", "-d", WSL_DISTRO, "--exec")


def docker_exec_argv(
    container: str, command: Sequence[str], platform: str = sys.platform
) -> list[str]:
    """The only place a process argv is built. Raises ``CommandNotAllowedError``
    for anything outside the Lab 1 read-only allowlist."""
    assert_command_allowed(container, command)
    prefix = _WINDOWS_DOCKER_PREFIX if platform == "win32" else ()
    return [*prefix, "docker", "exec", container, *command]


def subprocess_runner(container: str, command: Sequence[str]) -> CommandResult:
    argv = docker_exec_argv(container, command)
    try:
        completed = subprocess.run(
            argv,
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


# --------------------------------------------------------------------------
# JSON serialization
# --------------------------------------------------------------------------


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _isoformat(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result = {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
        if isinstance(value, RouteObservation):
            # Derived, never stored in the model; emitted for human readers.
            result["ecmp_path_count"] = value.ecmp_path_count
        return result
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value


def fabric_to_document(fabric: FabricOperationalState) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **to_jsonable(fabric)}


def is_complete(fabric: FabricOperationalState) -> bool:
    return all(not node.unavailable for node in fabric.nodes)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect one read-only normalized operational-state snapshot of the "
            "deployed Lab 1 and print it as JSON. The target is fixed."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON document to this file",
    )
    return parser


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_collection_id() -> str:
    return uuid.uuid4().hex


def main(
    argv: Sequence[str] | None = None,
    runner: NodeCommandRunner | None = None,
    clock: Callable[[], datetime] | None = None,
    collection_id_factory: Callable[[], str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        collector = ContainerlabFrrCollector(
            runner or subprocess_runner,
            clock or _utc_now,
            collection_id_factory or _new_collection_id,
        )
        fabric = collector.collect()
        text = json.dumps(fabric_to_document(fabric), indent=2)
    except Exception as error:  # noqa: BLE001 - last-resort CLI boundary
        print(f"internal error: {error!r}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    print(text)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    if not is_complete(fabric):
        print(
            "collection incomplete: at least one facet was unavailable "
            "(an unavailable facet is unknown, not a network failure)",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    return EXIT_COLLECTED


if __name__ == "__main__":
    sys.exit(main())
