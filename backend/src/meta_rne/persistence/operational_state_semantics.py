"""The single definition of latest-operational-state write semantics (NPE-1D1A).

Collection identity is ``(lab_name, collection_id)``; ``collection_id`` is an
opaque correlation ID and is never ordered. Both the in-memory and the
PostgreSQL repository decide through ``decide_latest_write`` so neither can be
looser than the other.

For an existing latest row of the same lab:

    different id, incoming newer   -> REPLACE
    different id, incoming older   -> StaleOperationalStateError
    different id, equal timestamp  -> OperationalStateConflictError
    same id, same time, same state -> UNCHANGED (exact idempotent replay)
    same id, different timestamp   -> OperationalStateConflictError
    same id, same time, different  -> OperationalStateConflictError
"""

from enum import StrEnum

from meta_rne.domain.operational_state import FabricOperationalState
from meta_rne.persistence.errors import OperationalStateConflictError, StaleOperationalStateError


class LatestWriteDecision(StrEnum):
    REPLACE = "REPLACE"
    UNCHANGED = "UNCHANGED"


def decide_latest_write(
    lab_name: str, stored: FabricOperationalState, incoming: FabricOperationalState
) -> LatestWriteDecision:
    if incoming.collection_id == stored.collection_id:
        if incoming.collected_at != stored.collected_at:
            raise OperationalStateConflictError(
                lab_name,
                incoming.collection_id,
                "the same collection_id was presented with a different collected_at",
            )
        if incoming != stored:
            raise OperationalStateConflictError(
                lab_name,
                incoming.collection_id,
                "the same collection_id and collected_at were presented with a different state",
            )
        return LatestWriteDecision.UNCHANGED
    if incoming.collected_at > stored.collected_at:
        return LatestWriteDecision.REPLACE
    if incoming.collected_at < stored.collected_at:
        raise StaleOperationalStateError(lab_name, incoming.collection_id, stored.collection_id)
    raise OperationalStateConflictError(
        lab_name,
        incoming.collection_id,
        f"distinct collection {stored.collection_id!r} has an identical collected_at, "
        "so there is no truthful ordering",
    )
