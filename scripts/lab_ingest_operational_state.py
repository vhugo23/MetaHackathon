#!/usr/bin/env python3
"""Collect Lab 1 operational state, persist it as the lab's latest state and
persist its BGP incidents (NPE-1C3B3, NPE-1D1A).

    DATABASE_URL=postgresql+psycopg://... python scripts/lab_ingest_operational_state.py

Run ``scripts/lab_register_devices.py`` first: this command does not register
devices, and an anomaly on an unregistered device fails closed (exit 3) with
nothing persisted.

It composes two existing, unchanged pieces and nothing else:

  1. ``lab_collect_state``'s fixed, allowlisted read-only collector
     (``ContainerlabFrrCollector`` + its one subprocess runner), then
  2. ``OperationalStateIngestionService``: the fabric is saved as the lab's
     latest operational state, then detector -> incident mapper ->
     fingerprint -> atomic upsert, plus positive-evidence recovery (an
     Established neighbor resolves its own OPEN RULE-BGP-DOWN incident), all in
     one transaction, one commit.

There is no option that accepts a container, node, command, interface,
neighbor address, vendor or device ID. It injects no fault, performs no HTTP,
and persists no telemetry. Only a neighbor observed Established resolves an
OPEN incident (same device, same neighbor); unknown, unavailable, missing or
transitional state never does.

Collection identity is (lab, collection_id). Replaying an already-accepted
collection exactly is a successful no-op ("state_outcome": "UNCHANGED"): no
incident is touched. A collection older than the stored latest state, or one
that conflicts with it, is rejected with exit 4 and nothing is persisted.

stdout is one deterministic JSON summary; structured incident events go to
stderr so they never mix with it.

Exit codes:
    0  collected and ingested; every facet on every node was observed
    1  ingested, but at least one facet was unavailable (unknown, not failure)
    2  unexpected error, bad arguments, or DATABASE_URL is not set
    3  an anomaly references a device that is not registered; nothing persisted
    4  the collection was rejected as stale or conflicting with the stored latest
       operational state; nothing persisted
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

import lab_collect_state  # noqa: E402

from meta_rne.adapters.containerlab_frr import (  # noqa: E402
    ContainerlabFrrCollector,
    NodeCommandRunner,
)
from meta_rne.api.dependencies import build_lazy_sqlalchemy_unit_of_work_factory  # noqa: E402
from meta_rne.application.errors import DeviceNotFoundError  # noqa: E402
from meta_rne.application.operational_state_ingestion import (  # noqa: E402
    OperationalStateIngestionResult,
    OperationalStateIngestionService,
)
from meta_rne.domain.anomaly import BgpDownEvidence  # noqa: E402
from meta_rne.domain.operational_state import FabricOperationalState  # noqa: E402
from meta_rne.domain.ports import UnitOfWork  # noqa: E402
from meta_rne.observability import StdoutIncidentEventSink  # noqa: E402
from meta_rne.persistence.errors import (  # noqa: E402
    OperationalStateConflictError,
    StaleOperationalStateError,
)

EXIT_INGESTED = 0
EXIT_INCOMPLETE = 1
EXIT_ERROR = 2
EXIT_DEVICE_NOT_REGISTERED = 3
EXIT_STATE_REJECTED = 4

SCHEMA_VERSION = 1


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def result_to_document(
    fabric: FabricOperationalState, result: OperationalStateIngestionResult
) -> dict[str, Any]:
    anomalies = []
    for anomaly in result.anomalies:
        evidence = anomaly.evidence
        assert isinstance(evidence, BgpDownEvidence)
        anomalies.append(
            {
                "device_id": anomaly.device_id,
                "rule_id": anomaly.rule_id.value,
                "neighbor_ip": evidence.neighbor_ip,
                "state": evidence.state.value,
                "previous_state": (
                    None
                    if evidence.previous_state is None
                    else evidence.previous_state.value
                ),
            }
        )
    incidents = [
        {
            "incident_id": upsert.incident.incident_id,
            "outcome": upsert.outcome.value,
            "device_id": upsert.incident.device_id,
            "affected_resource": upsert.incident.affected_resource,
            "fingerprint": upsert.incident.fingerprint,
            "occurrence_count": upsert.incident.occurrence_count,
            "status": upsert.incident.status.value,
            "last_seen_at": _isoformat(upsert.incident.last_seen_at),
        }
        for upsert in result.upserts
    ]
    resolved = [
        {
            "incident_id": incident.incident_id,
            "device_id": incident.device_id,
            "affected_resource": incident.affected_resource,
            "fingerprint": incident.fingerprint,
            "occurrence_count": incident.occurrence_count,
            "status": incident.status.value,
            "last_seen_at": _isoformat(incident.last_seen_at),
            "resolved_at": (
                None if incident.resolved_at is None else _isoformat(incident.resolved_at)
            ),
        }
        for incident in result.resolved_incidents
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_id": result.collection_id,
        "collected_at": _isoformat(fabric.collected_at),
        "state_outcome": result.state_outcome.value,
        "complete": lab_collect_state.is_complete(fabric),
        "anomaly_count": len(result.anomalies),
        "anomalies": anomalies,
        "incidents_created": result.incidents_created,
        "incidents_updated": result.incidents_updated,
        "incidents": incidents,
        "incidents_resolved": result.incidents_resolved,
        "resolved_incidents": resolved,
    }


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    runner: NodeCommandRunner | None = None,
    clock: Callable[[], datetime] | None = None,
    collection_id_factory: Callable[[], str] | None = None,
    unit_of_work_factory: Callable[[], UnitOfWork] | None = None,
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
            unit_of_work_factory = build_lazy_sqlalchemy_unit_of_work_factory(
                database_url
            )

        collector = ContainerlabFrrCollector(
            runner or lab_collect_state.subprocess_runner,
            clock or (lambda: datetime.now(UTC)),
            collection_id_factory or lab_collect_state._new_collection_id,
        )
        fabric = collector.collect()
        result = OperationalStateIngestionService(
            unit_of_work_factory, StdoutIncidentEventSink(sys.stderr)
        ).ingest(fabric)
    except DeviceNotFoundError as error:
        print(
            f"error: {error}; run scripts/lab_register_devices.py first (nothing persisted)",
            file=sys.stderr,
        )
        return EXIT_DEVICE_NOT_REGISTERED
    except StaleOperationalStateError as error:
        print(
            f"error: stale operational state rejected: {error} (nothing persisted)",
            file=sys.stderr,
        )
        return EXIT_STATE_REJECTED
    except OperationalStateConflictError as error:
        print(
            f"error: conflicting operational state rejected: {error} (nothing persisted)",
            file=sys.stderr,
        )
        return EXIT_STATE_REJECTED
    except Exception as error:  # noqa: BLE001 - last-resort CLI boundary
        print(f"internal error: {error!r}", file=sys.stderr)
        return EXIT_ERROR

    print(json.dumps(result_to_document(fabric, result), indent=2))
    if not lab_collect_state.is_complete(fabric):
        print(
            "collection incomplete: at least one facet was unavailable "
            "(an unavailable facet is unknown, not a network failure)",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    return EXIT_INGESTED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
