"""In-memory OperationalStateRepository (NPE-1D1A) — a conformance-test double,
never used in production (ADR-0002). Write semantics come from the same
``decide_latest_write`` the PostgreSQL repository uses, so it is exactly as
strict. Participates in ``InMemoryUnitOfWork`` snapshot/commit/rollback."""

from meta_rne.domain.operational_state import (
    FabricOperationalState,
    OperationalStateSaveOutcome,
    derive_fabric_source,
)
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.operational_state_semantics import (
    LatestWriteDecision,
    decide_latest_write,
)


class InMemoryOperationalStateRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def save_latest(self, fabric: FabricOperationalState) -> OperationalStateSaveOutcome:
        lab_name = derive_fabric_source(fabric).lab_name
        stored = self._store.operational_states.get(lab_name)
        if stored is None:
            self._store.operational_states[lab_name] = fabric
            return OperationalStateSaveOutcome.CREATED
        if decide_latest_write(lab_name, stored, fabric) is LatestWriteDecision.UNCHANGED:
            return OperationalStateSaveOutcome.UNCHANGED
        self._store.operational_states[lab_name] = fabric
        return OperationalStateSaveOutcome.REPLACED

    def get_latest(self, lab_name: str) -> FabricOperationalState | None:
        return self._store.operational_states.get(lab_name)
