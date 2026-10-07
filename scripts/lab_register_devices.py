#!/usr/bin/env python3
"""Register the four fixed Lab 1 routers as platform devices (NPE-1C3B1).

    DATABASE_URL=postgresql+psycopg://... python scripts/lab_register_devices.py

Idempotent. Registers exactly lab1-spine-1, lab1-spine-2, lab1-leaf-1 and
lab1-leaf-2 as snapshot-less FRR devices. There is no option that accepts a
device ID, vendor, container, command or address. It touches only the
platform database: no network access, no Docker/Containerlab, no subprocess,
no incident or telemetry writes. Hosts are not registered.

Exit codes:
    0  registered (or already registered)
    1  a fixed router ID exists in a conflicting state; nothing was changed
    2  unexpected error, or DATABASE_URL is not set
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from meta_rne.api.dependencies import build_lazy_sqlalchemy_unit_of_work_factory  # noqa: E402
from meta_rne.application.errors import LabDeviceConflictError  # noqa: E402
from meta_rne.application.lab_device_registration import RegisterLabDevicesService  # noqa: E402
from meta_rne.domain.ports import UnitOfWork  # noqa: E402

EXIT_OK = 0
EXIT_CONFLICT = 1
EXIT_ERROR = 2


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    unit_of_work_factory: Callable[[], UnitOfWork] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    if argv:
        print("error: this command takes no arguments", file=sys.stderr)
        return EXIT_ERROR
    env = os.environ if environ is None else environ

    try:
        if unit_of_work_factory is None:
            database_url = env.get("DATABASE_URL")
            if not database_url:
                print("error: DATABASE_URL is not set", file=sys.stderr)
                return EXIT_ERROR
            unit_of_work_factory = build_lazy_sqlalchemy_unit_of_work_factory(database_url)
        result = RegisterLabDevicesService(unit_of_work_factory).register((clock or _utc_now)())
    except LabDeviceConflictError as error:
        print(f"conflict: {error}", file=sys.stderr)
        return EXIT_CONFLICT
    except Exception as error:  # noqa: BLE001 - last-resort CLI boundary
        print(f"internal error: {error!r}", file=sys.stderr)
        return EXIT_ERROR

    for device_id in result.registered:
        print(f"registered {device_id}")
    for device_id in result.already_registered:
        print(f"already registered {device_id}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
