"""``ResolveIncidentService`` — Day 7A's application-level OPEN -> RESOLVED
use case, mirroring ``ConfigIngestionService``'s/``ListIncidentsService``'s
exception-preserving ``UnitOfWork`` lifecycle (Day 5A/5B).

``Clock.now()`` is called at most once per ``resolve()`` call, and only when
an OPEN incident actually needs to transition — never for an already-
RESOLVED incident (even if a misbehaving ``Clock`` would raise or return an
invalid value), and never a second time after the repository's atomic
``resolve()`` has already captured it. The production ``Clock``
implementation is composed by the API layer (Gate 7A-C) — this module never
imports ``meta_rne.api.clock`` or calls ``datetime.now()``/``utcnow()``
directly.

The repository's narrow, atomic ``resolve(incident_id, resolved_at)``
(Gate 7A-A) remains the sole persistence authority for the transition: this
service never constructs a mutated ``Incident`` itself and never issues a
full-row save.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from meta_rne.application.errors import IncidentNotFoundError
from meta_rne.domain.incident import Incident, IncidentStatus
from meta_rne.domain.ports import UnitOfWork


class Clock(Protocol):
    def now(self) -> datetime: ...


class ResolveIncidentService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def resolve(self, incident_id: str) -> Incident:
        uow = self._unit_of_work_factory()
        try:
            incident = uow.incidents.get_by_id(incident_id)
            if incident is None:
                raise IncidentNotFoundError(incident_id)

            if incident.status is IncidentStatus.RESOLVED:
                result = incident
            else:
                # A concurrent client may resolve this same incident between
                # this get_by_id() and the repository's atomic resolve()
                # call below (Gate 7A-A Section 6). The repository is the
                # sole authority on the outcome: it either performs the
                # transition with this captured Clock value, returns the
                # already-RESOLVED incident a concurrent request just
                # committed (accepted here as a successful idempotent
                # result), or raises its own monotonicity/invariant error if
                # concurrent ingestion has since advanced updated_at past
                # this Clock value — that error is never swallowed here.
                resolved_at = self._clock.now()
                resolved = uow.incidents.resolve(incident_id, resolved_at)
                if resolved is None:
                    # The row existed on the read above but is gone by the
                    # time resolve() ran (incidents are never deleted in
                    # this system, so this is not expected in practice) —
                    # treated as not-found rather than inventing a new
                    # failure category.
                    raise IncidentNotFoundError(incident_id)
                result = resolved
                uow.commit()
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
            return result
