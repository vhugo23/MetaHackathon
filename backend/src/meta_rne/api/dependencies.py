"""Controlled composition helpers for ``create_app`` (Day 5B).

No global mutable state and no module-level engine/Session — every helper
here is a pure builder called once by ``create_app`` (api/app.py), never a
FastAPI ``Depends`` provider reading a module-level singleton. Production
SQLAlchemy engine construction is lazy (``build_lazy_sqlalchemy_unit_of_work_factory``):
the engine is created on first actual use, not at import or
``create_app()`` call time, so importing ``api.app`` never requires
``DATABASE_URL`` to be set and never opens a connection.
"""

import os
from collections.abc import Callable
from datetime import datetime
from typing import cast

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.domain.ports import UnitOfWork
from meta_rne.persistence.seeds import build_slice1_policies
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork


def build_production_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry([CiscoAdapter()])


class _LazySqlAlchemyUnitOfWorkFactory:
    """Callable[[], UnitOfWork] that creates its engine on first call only,
    caching it for reuse, and exposing it for shutdown disposal — never at
    construction time."""

    def __init__(self, database_url: str | None) -> None:
        self._database_url = database_url
        self._engine: Engine | None = None

    def _resolve_database_url(self) -> str:
        if self._database_url is not None:
            return self._database_url
        try:
            return os.environ["DATABASE_URL"]
        except KeyError as exc:
            raise RuntimeError(
                "DATABASE_URL is not set and no database_url was provided to create_app"
            ) from exc

    @property
    def engine(self) -> Engine | None:
        return self._engine

    def __call__(self) -> UnitOfWork:
        if self._engine is None:
            self._engine = create_engine(self._resolve_database_url())
        engine = self._engine
        # SqlAlchemyUnitOfWork structurally satisfies UnitOfWork (each
        # repository attribute implements the corresponding Protocol) but
        # mypy checks concrete-class attribute types invariantly, not
        # structurally, so an explicit cast is needed here.
        return cast(UnitOfWork, SqlAlchemyUnitOfWork(lambda: Session(bind=engine)))


def build_lazy_sqlalchemy_unit_of_work_factory(
    database_url: str | None,
) -> _LazySqlAlchemyUnitOfWorkFactory:
    return _LazySqlAlchemyUnitOfWorkFactory(database_url)


def seed_slice1_policies(
    unit_of_work_factory: Callable[[], UnitOfWork], clock: Callable[[], datetime]
) -> None:
    """Idempotent Slice 1 policy seeding (architecture.md Section 11.2) —
    exception-preserving lifecycle handling matching ``ConfigIngestionService``/
    ``ListIncidentsService``: one ``UnitOfWork``, commit once on success,
    rollback-then-close (each attempted once, secondary failures recorded as
    exception notes) on failure, original exception never replaced."""
    from meta_rne.api.clock import require_utc

    observed_at = require_utc(clock())

    uow = unit_of_work_factory()
    try:
        policies = build_slice1_policies(observed_at)
        uow.configuration_policies.seed_if_missing(policies)
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
