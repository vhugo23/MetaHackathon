"""Shared UnitOfWork conformance tests (Day 4B3), run against both the
in-memory and SQLAlchemy implementations via ``unit_of_work_factory``
(conftest.py in this directory) — one set of test bodies proving both
satisfy the same commit/rollback/close contract (test-strategy.md
Section 9). Implementation-specific behavior (a real commit-failure
exception path, Session-sharing, isolation between two *simultaneously
open* UnitOfWorks) is covered separately in
``tests/unit/persistence/test_in_memory_unit_of_work.py`` and
``tests/integration/persistence/test_sqlalchemy_unit_of_work.py``.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from meta_rne.domain.config import (
    AclDirection,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity, ViolationType
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash

DEVICE_ID = "spine-01"
SNAPSHOT_ID = "snap-1"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(**overrides: object) -> Device:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "vendor": VendorType.CISCO_IOS_XE,
        "current_snapshot_id": None,
        "baseline_snapshot_id": None,
        "created_at": T0,
        "updated_at": T0,
    }
    defaults.update(overrides)
    return Device(**defaults)  # type: ignore[arg-type]


def _snapshot() -> ConfigurationSnapshot:
    raw_text = f"hostname {DEVICE_ID}\n"
    return ConfigurationSnapshot(
        snapshot_id=SNAPSHOT_ID,
        device_id=DEVICE_ID,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw_text,
        raw_text_hash=compute_raw_text_hash(raw_text),
        normalized_config=NormalizedConfiguration(
            hostname=DEVICE_ID,
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        ),
        submitted_at=T0,
    )


def _policy() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-acl-external-in",
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


def _candidate() -> IncidentCandidate:
    return IncidentCandidate(
        device_id=DEVICE_ID,
        source=IncidentSource.POLICY_VIOLATION,
        rule_ref="policy-acl-external-in",
        affected_resource="interface:GigabitEthernet0/1:acl_in",
        severity=Severity.MEDIUM,
        evidence=PolicyViolationIncidentEvidence(
            source_snapshot_id=SNAPSHOT_ID,
            violation_type=ViolationType.MISSING_REQUIRED_ACL,
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        ),
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        observed_at=T0,
    )


def _stage_full_transaction(uow: Any) -> None:
    """Stages a Device with null references, a ConfigurationSnapshot, the
    Device updated with references to that snapshot, a ConfigurationPolicy,
    and an Incident — all inside the caller's one UnitOfWork (item 12's
    binding multi-repository staging list)."""
    uow.devices.save(_device())
    uow.configuration_snapshots.add(_snapshot())
    uow.devices.save(_device(current_snapshot_id=SNAPSHOT_ID, baseline_snapshot_id=SNAPSHOT_ID))
    uow.configuration_policies.seed_if_missing((_policy(),))
    candidate = _candidate()
    fingerprint = compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )
    uow.incidents.upsert_open_incident(candidate, fingerprint, T0)


def _assert_full_transaction_visible(uow: Any) -> None:
    device = uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == SNAPSHOT_ID
    assert device.baseline_snapshot_id == SNAPSHOT_ID

    assert uow.configuration_snapshots.get_by_id(SNAPSHOT_ID) is not None

    policies = uow.configuration_policies.get_applicable_to_device(DEVICE_ID)
    assert len(policies) == 1
    assert policies[0].policy_id == "policy-acl-external-in"

    incidents = uow.incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].occurrence_count == 1


def _assert_full_transaction_absent(uow: Any) -> None:
    assert uow.devices.get_by_id(DEVICE_ID) is None
    assert uow.configuration_snapshots.get_by_id(SNAPSHOT_ID) is None
    assert uow.configuration_policies.get_applicable_to_device(DEVICE_ID) == ()
    assert uow.incidents.list_all() == ()


def test_unit_of_work__all_four_repositories_are_available(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    uow = unit_of_work_factory()

    assert uow.devices is not None
    assert uow.configuration_snapshots is not None
    assert uow.configuration_policies is not None
    assert uow.incidents is not None


def test_unit_of_work__commit__publishes_the_full_staged_transaction(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    uow = unit_of_work_factory()
    _stage_full_transaction(uow)

    uow.commit()

    verify_uow = unit_of_work_factory()
    _assert_full_transaction_visible(verify_uow)


def test_unit_of_work__rollback__discards_the_full_staged_transaction(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    uow = unit_of_work_factory()
    _stage_full_transaction(uow)

    uow.rollback()

    _assert_full_transaction_absent(uow)


def test_unit_of_work__close_without_commit__publishes_nothing(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    uow = unit_of_work_factory()
    _stage_full_transaction(uow)

    uow.close()

    verify_uow = unit_of_work_factory()
    _assert_full_transaction_absent(verify_uow)


def test_unit_of_work__new_unit_of_work__sees_committed_data(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    first = unit_of_work_factory()
    first.devices.save(_device())
    first.commit()

    second = unit_of_work_factory()

    assert second.devices.get_by_id(DEVICE_ID) == _device()


def test_unit_of_work__new_unit_of_work__does_not_see_rolled_back_data(
    unit_of_work_factory: Callable[[], Any],
) -> None:
    first = unit_of_work_factory()
    first.devices.save(_device())
    first.rollback()

    second = unit_of_work_factory()

    assert second.devices.get_by_id(DEVICE_ID) is None
