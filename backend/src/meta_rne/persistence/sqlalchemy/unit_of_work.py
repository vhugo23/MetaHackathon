"""SQLAlchemy/PostgreSQL UnitOfWork (Day 4B3).

Constructed from a ``session_factory: Callable[[], Session]`` — never an
already-created ``Session`` — so it fully owns the Session it creates and
gives that same Session to all four repositories, letting one transaction
span every write inside one ``UnitOfWork`` (architecture.md Section 11.1).
``commit()`` calls the real ``Session.commit()``; on any exception it rolls
back and re-raises the original exception unchanged (never swallowed or
replaced). No context-manager syntax (``__enter__``/``__exit__``) is added
this phase.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository
from meta_rne.persistence.sqlalchemy.policy_repository import (
    SqlAlchemyConfigurationPolicyRepository,
)
from meta_rne.persistence.sqlalchemy.snapshot_repository import (
    SqlAlchemyConfigurationSnapshotRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session = session_factory()
        self.devices = SqlAlchemyDeviceRepository(self._session)
        self.configuration_snapshots = SqlAlchemyConfigurationSnapshotRepository(self._session)
        self.configuration_policies = SqlAlchemyConfigurationPolicyRepository(self._session)
        self.incidents = SqlAlchemyIncidentRepository(self._session)

    def commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
