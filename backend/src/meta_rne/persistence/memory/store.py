"""Shared in-memory store backing the Day 4B2/4B3 in-memory repositories.

One store per test (or per Unit of Work, Day 4B3), holding all four
collections, so ``InMemoryConfigurationSnapshotRepository`` can check that a
snapshot's ``device_id`` references a real ``Device``,
``InMemoryDeviceRepository`` can check that a non-null
``current_snapshot_id``/``baseline_snapshot_id`` references a real
``ConfigurationSnapshot``, and ``InMemoryIncidentRepository`` can check that
an incident's ``device_id`` references a real ``Device`` — the same
cross-reference integrity PostgreSQL's foreign keys provide, enforced
explicitly here since there is no database.

Domain objects are immutable, so storing them directly (keyed by identity)
needs no separate "model" layer for the in-memory side.

``incidents_lock`` guards the whole find-OPEN-by-fingerprint -> decide ->
mutate sequence in ``InMemoryIncidentRepository.upsert_open_incident``
(domain-model.md Section 11's "single critical section" requirement) — a
dedicated lock, not the whole store, since only that one operation needs
atomicity across a check-then-act sequence.

``publish_lock`` (Day 4B3) guards ``InMemoryUnitOfWork.commit()``'s
all-four-collections publish into a store used as a UnitOfWork's *committed*
store — a separate lock from ``incidents_lock`` since it protects a
different critical section (publishing four collection references at once,
not one repository's check-then-act sequence).
"""

import threading
from dataclasses import dataclass, field

from meta_rne.domain.device import Device
from meta_rne.domain.incident import Incident
from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.domain.snapshot import ConfigurationSnapshot


@dataclass
class InMemoryStore:
    devices: dict[str, Device] = field(default_factory=dict)
    snapshots: dict[str, ConfigurationSnapshot] = field(default_factory=dict)
    policies: dict[str, ConfigurationPolicy] = field(default_factory=dict)
    incidents: dict[str, Incident] = field(default_factory=dict)
    incidents_lock: threading.Lock = field(default_factory=threading.Lock)
    publish_lock: threading.Lock = field(default_factory=threading.Lock)
