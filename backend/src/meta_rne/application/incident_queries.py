"""``ListIncidentsService`` — Day 5B's read-only incident-listing use case.

Mirrors ``ConfigIngestionService``'s exception-preserving ``UnitOfWork``
lifecycle style (Day 5A): one ``UnitOfWork`` per call, ``close()`` always
attempted exactly once. Unlike ``ConfigIngestionService`` there is nothing
to ``commit()`` — this is a pure read — but ``rollback()`` is still
attempted on failure, since a SQLAlchemy read can open a transaction that
needs explicit rollback before the ``Session`` is closed.
"""

from collections.abc import Callable

from meta_rne.domain.incident import Incident
from meta_rne.domain.ports import UnitOfWork


class ListIncidentsService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def list_all(self) -> tuple[Incident, ...]:
        uow = self._unit_of_work_factory()
        try:
            incidents = uow.incidents.list_all()
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
            return incidents
