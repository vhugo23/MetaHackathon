"""Injectable incident-ID generation boundary (Day 4B3).

``incident_id`` is platform-generated at creation (domain-model.md Section
16's identity table) — neither ``IncidentCandidate`` nor ``Incident``
generates its own ID, and no repository calls ``uuid4`` directly. Both
``SqlAlchemyIncidentRepository`` and ``InMemoryIncidentRepository`` accept an
``incident_id_factory: Callable[[], str]`` constructor argument, defaulting
to ``default_incident_id_factory`` here in production; tests inject a
deterministic factory instead.
"""

from uuid import uuid4


def default_incident_id_factory() -> str:
    return str(uuid4())
