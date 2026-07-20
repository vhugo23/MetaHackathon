"""Focused PostgreSQL transaction tests for ``ConfigIngestionService``
(Day 5A plan item 13). Uses real ``SqlAlchemyUnitOfWork``, PostgreSQL
repositories, ``AdapterRegistry``/``CiscoAdapter``, and real database
transaction behavior — not a re-run of the in-memory suite
(``tests/unit/application/test_config_ingestion_service.py`` already proves
call-count/ordering/rollback behavior against the fast in-memory double).
These three tests exist to prove what only a real database transaction can
prove: atomic multi-table commit, and atomic multi-table rollback after a
late application-layer failure.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.models import IngestConfigurationCommand
from meta_rne.detection.incident_factory import IncidentFactory
from meta_rne.domain.config import AclDirection, VendorType
from meta_rne.domain.incident import IncidentStatus
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"

_MULTI_VIOLATION_RAW_CONFIG = (
    "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\ninterface GigabitEthernet0/2\n!\n"
)


def _policy_a() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-a-acl-external-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-EXTERNAL-IN",
                interface_name="GigabitEthernet0/1",
                direction=AclDirection.IN,
                severity=Severity.MEDIUM,
                recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
            ),
        ),
        created_at=T0,
    )


def _policy_b() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-b-acl-mgmt-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-MGMT-IN",
                interface_name="GigabitEthernet0/2",
                direction=AclDirection.IN,
                severity=Severity.HIGH,
                recommendation="Assign ACL-MGMT-IN inbound to GigabitEthernet0/2",
            ),
        ),
        created_at=T0,
    )


def _command(**overrides: object) -> IngestConfigurationCommand:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "vendor": "cisco-ios-xe",
        "raw_config_text": _MISSING_ACL_RAW_CONFIG,
        "observed_at": T0,
    }
    defaults.update(overrides)
    return IngestConfigurationCommand(**defaults)  # type: ignore[arg-type]


def _seed_policies(session_factory: Callable[[], Session], *policies: ConfigurationPolicy) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.configuration_policies.seed_if_missing(policies)
    uow.commit()
    uow.close()


def _service(
    session_factory: Callable[[], Session],
    *,
    snapshot_id_factory: Callable[[], str],
    incident_candidate_factory: Callable[..., Any] | None = None,
) -> ConfigIngestionService:
    kwargs: dict[str, Any] = {
        "unit_of_work_factory": lambda: SqlAlchemyUnitOfWork(session_factory),
        "adapter_registry": AdapterRegistry([CiscoAdapter()]),
        "snapshot_id_factory": snapshot_id_factory,
    }
    if incident_candidate_factory is not None:
        kwargs["incident_candidate_factory"] = incident_candidate_factory
    return ConfigIngestionService(**kwargs)


def test_config_ingestion_postgres__missing_acl__atomically_commits_device_snapshot_and_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_policies(sqlalchemy_session_factory, _policy_a())
    service = _service(sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-1")

    result = service.ingest(_command())

    assert result.violations_detected == 1
    assert result.incidents_created == 1

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-1"
    assert device.baseline_snapshot_id == "snap-1"
    assert device.vendor == VendorType.CISCO_IOS_XE

    snapshot = verify_uow.configuration_snapshots.get_by_id("snap-1")
    assert snapshot is not None
    assert snapshot.raw_config_text == _MISSING_ACL_RAW_CONFIG

    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].status is IncidentStatus.OPEN
    assert incidents[0].occurrence_count == 1
    verify_uow.close()


def test_config_ingestion_postgres__late_failure_after_incident_staged__persists_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_policies(sqlalchemy_session_factory, _policy_a(), _policy_b())
    call_count = 0

    def flaky_factory(violation: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("forced late failure on second violation")
        return IncidentFactory.build_candidate(violation)

    service = _service(
        sqlalchemy_session_factory,
        snapshot_id_factory=lambda: "snap-1",
        incident_candidate_factory=flaky_factory,
    )

    with pytest.raises(RuntimeError, match="forced late failure"):
        service.ingest(_command(raw_config_text=_MULTI_VIOLATION_RAW_CONFIG))

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.devices.get_by_id(DEVICE_ID) is None
    assert verify_uow.configuration_snapshots.get_by_id("snap-1") is None
    assert verify_uow.incidents.list_all() == ()
    verify_uow.close()


def test_config_ingestion_postgres__repeated_ingestion__advances_snapshot_and_updates_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_policies(sqlalchemy_session_factory, _policy_a())
    ids = iter(["snap-1", "snap-2"])
    service = _service(sqlalchemy_session_factory, snapshot_id_factory=lambda: next(ids))

    service.ingest(_command(observed_at=T0))
    first_incident = SqlAlchemyUnitOfWork(sqlalchemy_session_factory).incidents.list_all()[0]

    result = service.ingest(_command(observed_at=T1))

    assert result.snapshot_id == "snap-2"
    assert result.incidents_created == 0
    assert result.incidents_updated == 1

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-2"
    assert device.baseline_snapshot_id == "snap-1"

    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].incident_id == first_incident.incident_id
    assert incidents[0].occurrence_count == 2
    assert incidents[0].status is IncidentStatus.OPEN
    verify_uow.close()
