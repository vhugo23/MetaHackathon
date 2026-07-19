"""Persistence-facing error hierarchy (Day 4B2).

Repositories translate expected business conflicts into these types —
never a leaked SQLAlchemy ``IntegrityError``, psycopg exception, or
constraint name. ``PersistenceError`` (not a conflict) is the fallback for
a genuinely unexpected/infrastructure failure a repository could not
identify as one of the named conflicts below, so callers can still tell
"a rule was violated" apart from "something unexpected went wrong."
"""


class PersistenceError(Exception):
    """Base type for every persistence-layer failure. An unrecognized
    infrastructure failure is raised as this base type directly, never
    mislabeled as one of the conflict subclasses below."""


class PersistenceConflictError(PersistenceError):
    """Base type for an identified business-rule conflict — the operation
    was well-formed, but the current stored state rejects it."""


class DeviceConflictError(PersistenceConflictError):
    """Raised by ``DeviceRepository.save`` for any rejected Device lifecycle
    transition: vendor change, created_at change, updated_at regression,
    replacing a non-null baseline_snapshot_id, clearing a non-null
    current_snapshot_id, or a non-null snapshot reference that does not
    exist. Covers every rejected transition — there is no separate
    vendor-only error type."""


class SnapshotAlreadyExistsError(PersistenceConflictError):
    """Raised by ``ConfigurationSnapshotRepository.add`` when snapshot_id
    already exists — snapshots are append-only."""

    def __init__(self, snapshot_id: str) -> None:
        super().__init__(f"snapshot already exists: {snapshot_id!r}")
        self.snapshot_id = snapshot_id


class ReferencedDeviceNotFoundError(PersistenceConflictError):
    """Raised by ``ConfigurationSnapshotRepository.add`` when device_id does
    not reference an existing Device."""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"referenced device not found: {device_id!r}")
        self.device_id = device_id


class PolicySeedConflictError(PersistenceConflictError):
    """Raised by ``ConfigurationPolicyRepository.seed_if_missing`` when a
    policy_id already exists with semantically different content
    (``applies_to`` or ``required_acls`` differ; ``created_at`` is
    insertion metadata and is not part of this comparison)."""

    def __init__(self, policy_id: str) -> None:
        super().__init__(f"policy seed conflict: {policy_id!r}")
        self.policy_id = policy_id
