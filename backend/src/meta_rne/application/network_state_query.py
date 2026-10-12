"""``GetNetworkStateService`` — NPE-1D1B's read-only latest-network-state query.

One ``UnitOfWork`` per call, no ``commit()``: a pure read of the latest
``FabricOperationalState`` for the one configured lab. An absent state raises
``OperationalStateNotFoundError`` — it is never an empty or "healthy" result.
``retrieved_at`` comes from the injected clock, read exactly once per
successful lookup; it is not stored, not part of the fabric, and not a
freshness threshold.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from meta_rne.application.errors import OperationalStateNotFoundError
from meta_rne.domain.operational_state import (
    FabricOperationalState,
    FabricSourceIdentity,
    derive_fabric_source,
)
from meta_rne.domain.ports import UnitOfWork


@dataclass(frozen=True, slots=True)
class NetworkStateResult:
    fabric: FabricOperationalState
    source: FabricSourceIdentity
    retrieved_at: datetime


class GetNetworkStateService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        *,
        lab_name: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lab_name = lab_name
        self._clock = clock

    def get(self) -> NetworkStateResult:
        uow = self._unit_of_work_factory()
        try:
            fabric = uow.operational_state.get_latest(self._lab_name)
            if fabric is None:
                raise OperationalStateNotFoundError(self._lab_name)
            # Fails closed (FabricSourceError) on persisted data with no single source.
            source = derive_fabric_source(fabric)
            retrieved_at = self._clock()
        except Exception as original_error:
            try:
                uow.rollback()
            except Exception as rollback_error:
                original_error.add_note(f"UnitOfWork rollback also failed: {rollback_error!r}")
            try:
                uow.close()
            except Exception as close_error:
                original_error.add_note(f"UnitOfWork close also failed: {close_error!r}")
            raise
        else:
            uow.close()
            return NetworkStateResult(fabric=fabric, source=source, retrieved_at=retrieved_at)
