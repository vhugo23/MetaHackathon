"""Injectable snapshot-ID generation boundary (Day 5A).

``ConfigurationSnapshotRepository.add`` never generates its own ID (unlike
``IncidentRepository.upsert_open_incident``'s ``incident_id_factory``), so
``ConfigIngestionService`` generates the snapshot ID itself, via an injected
``snapshot_id_factory: Callable[[], str]`` defaulting to
``default_snapshot_id_factory`` here in production; tests inject a
deterministic factory instead. Mirrors
``meta_rne.persistence.incident_id``'s convention.
"""

from uuid import uuid4


def default_snapshot_id_factory() -> str:
    return str(uuid4())
