"""Shared in-memory store backing the Day 4B2 in-memory repositories.

One store per test, holding all three collections, so
``InMemoryConfigurationSnapshotRepository`` can check that a snapshot's
``device_id`` references a real ``Device`` and
``InMemoryDeviceRepository`` can check that a non-null
``current_snapshot_id``/``baseline_snapshot_id`` references a real
``ConfigurationSnapshot`` — the same cross-reference integrity PostgreSQL's
foreign keys provide, enforced explicitly here since there is no database.

Domain objects are immutable, so storing them directly (keyed by identity)
needs no separate "model" layer for the in-memory side.
"""

from dataclasses import dataclass, field

from meta_rne.domain.device import Device
from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.domain.snapshot import ConfigurationSnapshot


@dataclass
class InMemoryStore:
    devices: dict[str, Device] = field(default_factory=dict)
    snapshots: dict[str, ConfigurationSnapshot] = field(default_factory=dict)
    policies: dict[str, ConfigurationPolicy] = field(default_factory=dict)
