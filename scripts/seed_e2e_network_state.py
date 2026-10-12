#!/usr/bin/env python3
"""Seed ONE deterministic baseline operational state into the browser-E2E
database (NPE-1D2). TEST-ONLY.

This runs *inside the compose ``api`` container* (``scripts/browser_e2e.py``
copies it, ``backend/tests/operational_state_support.py`` and the captured
baseline fixtures to ``/tmp/e2e-seed`` first), against the container's own
``DATABASE_URL``. It goes through the real application path:

    captured baseline fixture -> FabricOperationalState
        -> OperationalStateIngestionService.ingest -> operational_state_latest

so the real ``GET /network/state`` then serves it. There is no public write
endpoint, no frontend mock, no SQL, and no Containerlab. The baseline has no
BGP anomaly, so no device registration or incident is involved. Fixed
``collected_at`` and ``collection_id`` keep the seed fully deterministic.

stdout: one JSON line ``{"state_outcome": ..., "collection_id": ...}``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from operational_state_support import rich_fixture_fabric  # noqa: E402

from meta_rne.api.dependencies import build_lazy_sqlalchemy_unit_of_work_factory  # noqa: E402
from meta_rne.application.operational_state_ingestion import (  # noqa: E402
    OperationalStateIngestionService,
)
from meta_rne.lab1_identity import LAB1_LAB_NAME  # noqa: E402

COLLECTION_ID = "browser-e2e-baseline"
COLLECTED_AT = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
COLLECTOR = "containerlab-frr"


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    fabric = rich_fixture_fabric(
        "baseline",
        COLLECTED_AT,
        COLLECTION_ID,
        lab_name=LAB1_LAB_NAME,
        collector=COLLECTOR,
    )
    result = OperationalStateIngestionService(
        build_lazy_sqlalchemy_unit_of_work_factory(database_url)
    ).ingest(fabric)
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
    raise SystemExit(main())
