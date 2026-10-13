#!/usr/bin/env python3
"""Seed ONE deterministic operational state into the browser-E2E database
(NPE-1D2 baseline; NPE-1E1C adds the degraded scenario). TEST-ONLY.

This runs *inside the compose ``api`` container* (``scripts/browser_e2e.py``
copies it, ``backend/tests/operational_state_support.py`` and the captured
fixtures to ``/tmp/e2e-seed`` first), against the container's own
``DATABASE_URL``. It goes through the real application path:

    captured fixture -> FabricOperationalState
        -> OperationalStateIngestionService.ingest -> operational_state_latest

so the real ``GET /network/state`` and ``GET /network/diagnosis`` then serve
it. There is no public write endpoint, no frontend mock, no SQL, and no
Containerlab. Fixed ``collected_at`` and ``collection_id`` keep every seed
fully deterministic.

Scenarios (``--scenario``, default ``baseline``):

``baseline``
    The healthy captured baseline. No BGP anomaly, so no device registration
    or incident is involved (NPE-1D2 behavior, unchanged).
``degraded``
    The captured ``leaf-1 eth1 down`` state, collected later than the
    baseline. The fixed Lab 1 routers are registered first (the supported
    ``RegisterLabDevicesService``), so the same ingestion that stores the state
    also opens the two device-scoped RULE-BGP-DOWN incidents the real
    ``GET /network/diagnosis`` then links.

stdout: one JSON line
``{"state_outcome": ..., "collection_id": ..., "anomalies": ...}``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from operational_state_support import rich_fixture_fabric  # noqa: E402

from meta_rne.api.dependencies import build_lazy_sqlalchemy_unit_of_work_factory  # noqa: E402
from meta_rne.application.lab_device_registration import (  # noqa: E402
    RegisterLabDevicesService,
)
from meta_rne.application.operational_state_ingestion import (  # noqa: E402
    OperationalStateIngestionService,
)
from meta_rne.lab1_identity import LAB1_LAB_NAME  # noqa: E402

COLLECTOR = "containerlab-frr"


@dataclass(frozen=True)
class Scenario:
    fixture_set: str
    collection_id: str
    collected_at: datetime
    register_devices: bool


SCENARIOS = {
    "baseline": Scenario(
        fixture_set="baseline",
        collection_id="browser-e2e-baseline",
        collected_at=datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC),
        register_devices=False,
    ),
    "degraded": Scenario(
        fixture_set="degraded-active",
        collection_id="browser-e2e-degraded",
        collected_at=datetime(2026, 10, 7, 12, 1, 0, tzinfo=UTC),
        register_devices=True,
    ),
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="baseline")
    scenario = SCENARIOS[parser.parse_args(argv).scenario]

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    factory = build_lazy_sqlalchemy_unit_of_work_factory(database_url)
    if scenario.register_devices:
        RegisterLabDevicesService(factory).register(scenario.collected_at)
    fabric = rich_fixture_fabric(
        scenario.fixture_set,
        scenario.collected_at,
        scenario.collection_id,
        lab_name=LAB1_LAB_NAME,
        collector=COLLECTOR,
    )
    result = OperationalStateIngestionService(factory).ingest(fabric)
    print(
        json.dumps(
            {
                "state_outcome": result.state_outcome.value,
                "collection_id": result.collection_id,
                "anomalies": len(result.anomalies),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
