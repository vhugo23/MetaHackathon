"""Repository conformance tests for IncidentRepository (Day 4B3).

Run against both the in-memory and SQLAlchemy implementations via the shared
``repositories`` fixture (conftest.py in this directory), extended with an
``incidents`` attribute. ``upsert_open_incident`` is the only write path
(domain-model.md Section 11) — no plain ``save()`` exists on this port.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from meta_rne.domain.config import (
    AclDirection,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)
FIRST_ID = "11111111-1111-1111-1111-111111111111"
SECOND_ID = "22222222-2222-2222-2222-222222222222"


def _seed_device(repositories: SimpleNamespace, device_id: str = DEVICE_ID) -> None:
    repositories.devices.save(
        Device(
            device_id=device_id,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )


def _snapshot(snapshot_id: str, device_id: str = DEVICE_ID) -> ConfigurationSnapshot:
    raw_text = f"hostname {device_id}\n! {snapshot_id}\n"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw_text,
        raw_text_hash=compute_raw_text_hash(raw_text),
        normalized_config=NormalizedConfiguration(
            hostname=device_id,
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        ),
        submitted_at=T0,
    )


def _evidence(**overrides: object) -> PolicyViolationIncidentEvidence:
    defaults: dict[str, object] = {
        "source_snapshot_id": "snap-1",
        "violation_type": ViolationType.MISSING_REQUIRED_ACL,
        "expected_acl_name": "ACL-EXTERNAL-IN",
        "actual_acl_name": None,
        "interface_name": "GigabitEthernet0/1",
        "direction": AclDirection.IN,
    }
    defaults.update(overrides)
    return PolicyViolationIncidentEvidence(**defaults)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> IncidentCandidate:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "source": IncidentSource.POLICY_VIOLATION,
        "rule_ref": "policy-acl-external-in",
        "affected_resource": "interface:GigabitEthernet0/1:acl_in",
        "severity": Severity.MEDIUM,
        "evidence": _evidence(),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "observed_at": T0,
    }
    defaults.update(overrides)
    return IncidentCandidate(**defaults)  # type: ignore[arg-type]


def _fingerprint(candidate: IncidentCandidate) -> str:
    return compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )


def _sequential_id_factory(ids: list[str]) -> Callable[[], str]:
    iterator: Iterator[str] = iter(ids)

    def factory() -> str:
        return next(iterator)

    return factory


def test_incident_repository__missing_incident__returns_none(
    repositories: SimpleNamespace,
) -> None:
    assert repositories.incidents.get_by_id("does-not-exist") is None


def test_incident_repository__first_upsert__returns_created(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()

    result = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert result.outcome == IncidentUpsertOutcome.CREATED


def test_incident_repository__created_incident__contains_every_candidate_field(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)

    result = incidents.upsert_open_incident(candidate, fingerprint, T0)

    incident = result.incident
    assert incident.fingerprint == fingerprint
    assert incident.device_id == candidate.device_id
    assert incident.source == candidate.source
    assert incident.rule_ref == candidate.rule_ref
    assert incident.affected_resource == candidate.affected_resource
    assert incident.severity == candidate.severity
    assert incident.evidence == candidate.evidence
    assert incident.recommendation == candidate.recommendation


def test_incident_repository__created_incident__occurrence_count_starts_at_one(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()

    result = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert result.incident.occurrence_count == 1


def test_incident_repository__created_incident__created_at_and_last_seen_at_equal_observed_at(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()

    result = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert result.incident.created_at == T0
    assert result.incident.last_seen_at == T0


def test_incident_repository__created_incident__stores_injected_deterministic_id(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()

    result = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert result.incident.incident_id == FIRST_ID
    fetched = incidents.get_by_id(FIRST_ID)
    assert fetched == result.incident


def test_incident_repository__get_by_id__returns_incident_not_orm_model(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()
    incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    fetched = incidents.get_by_id(FIRST_ID)

    assert isinstance(fetched, Incident)


def test_incident_repository__repeated_upsert__returns_updated(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    assert result.outcome == IncidentUpsertOutcome.UPDATED


def test_incident_repository__repeated_upsert__preserves_identity_and_created_at(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    assert result.incident.incident_id == FIRST_ID
    assert result.incident.created_at == T0
    assert result.incident.fingerprint == fingerprint
    assert result.incident.device_id == candidate.device_id
    assert result.incident.source == candidate.source
    assert result.incident.rule_ref == candidate.rule_ref
    assert result.incident.affected_resource == candidate.affected_resource
    assert result.incident.status == IncidentStatus.OPEN
    # never a second row for the same fingerprint
    assert len([i for i in incidents.list_all() if i.fingerprint == fingerprint]) == 1


def test_incident_repository__repeated_upsert__updates_severity(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate(severity=Severity.MEDIUM)
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(
        _candidate(severity=Severity.CRITICAL, observed_at=T1), fingerprint, T1
    )

    assert result.incident.severity == Severity.CRITICAL


def test_incident_repository__repeated_upsert__updates_evidence(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)
    new_evidence = _evidence(actual_acl_name="ACL-OTHER")

    result = incidents.upsert_open_incident(
        _candidate(evidence=new_evidence, observed_at=T1), fingerprint, T1
    )

    assert result.incident.evidence == new_evidence


def test_incident_repository__repeated_upsert__updates_recommendation(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(
        _candidate(recommendation="Different recommendation", observed_at=T1), fingerprint, T1
    )

    assert result.incident.recommendation == "Different recommendation"


def test_incident_repository__repeated_upsert__updates_last_seen_at(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    assert result.incident.last_seen_at == T1


def test_incident_repository__repeated_upsert__increments_occurrence_count_by_exactly_one(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    assert result.incident.occurrence_count == 2


def test_incident_repository__equal_timestamps__are_accepted_and_increment(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T0)

    result = incidents.upsert_open_incident(candidate, fingerprint, T0)

    assert result.outcome == IncidentUpsertOutcome.UPDATED
    assert result.incident.occurrence_count == 2
    assert result.incident.last_seen_at == T0


def test_incident_repository__stale_timestamp__is_rejected_without_mutation(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID, SECOND_ID]))
    candidate = _candidate(observed_at=T1)
    fingerprint = _fingerprint(candidate)
    incidents.upsert_open_incident(candidate, fingerprint, T1)

    with pytest.raises(ValueError):
        incidents.upsert_open_incident(_candidate(observed_at=T0), fingerprint, T0)

    stored = incidents.get_by_id(FIRST_ID)
    assert stored is not None
    assert stored.occurrence_count == 1
    assert stored.last_seen_at == T1
    assert stored.severity == candidate.severity
    assert stored.evidence == candidate.evidence
    assert stored.recommendation == candidate.recommendation


def test_incident_repository__fingerprint_mismatch__is_rejected_without_mutation(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()
    wrong_fingerprint = _fingerprint(_candidate(rule_ref="a-different-rule"))

    with pytest.raises(ValueError):
        incidents.upsert_open_incident(candidate, wrong_fingerprint, T0)

    assert incidents.list_all() == ()


def test_incident_repository__observed_at_mismatch_with_candidate__is_rejected_without_mutation(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate(observed_at=T0)

    with pytest.raises(ValueError):
        incidents.upsert_open_incident(candidate, _fingerprint(candidate), T1)

    assert incidents.list_all() == ()


def test_incident_repository__unsupported_source__is_rejected(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate(source=IncidentSource.DRIFT)

    with pytest.raises(ValueError):
        incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert incidents.list_all() == ()


def test_incident_repository__empty_generated_id__is_rejected(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(lambda: "   ")
    candidate = _candidate()

    with pytest.raises(ValueError):
        incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert incidents.list_all() == ()


def test_incident_repository__unknown_device__raises_referenced_device_not_found_error(
    repositories: SimpleNamespace,
) -> None:
    from meta_rne.persistence.errors import ReferencedDeviceNotFoundError

    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate(device_id="does-not-exist")

    with pytest.raises(ReferencedDeviceNotFoundError):
        incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert incidents.list_all() == ()


def test_incident_repository__list_all__returns_a_tuple(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    candidate = _candidate()
    incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    assert isinstance(incidents.list_all(), tuple)


def test_incident_repository__list_all__uses_created_at_then_incident_id_ordering(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    _seed_device(repositories, device_id="spine-02")
    incidents = repositories.make_incidents(_sequential_id_factory([SECOND_ID, FIRST_ID]))
    first_candidate = _candidate(device_id="spine-02", observed_at=T1)
    incidents.upsert_open_incident(first_candidate, _fingerprint(first_candidate), T1)
    second_candidate = _candidate(device_id=DEVICE_ID, observed_at=T0)
    incidents.upsert_open_incident(second_candidate, _fingerprint(second_candidate), T0)

    ordered = incidents.list_all()

    assert [i.incident_id for i in ordered] == [FIRST_ID, SECOND_ID]


def test_incident_repository__delimiter_quote_backslash_and_unicode_values__survive_persistence(
    repositories: SimpleNamespace,
) -> None:
    _seed_device(repositories)
    incidents = repositories.make_incidents(_sequential_id_factory([FIRST_ID]))
    tricky = 'rule|ref:with"quotes\\andünicode'
    candidate = _candidate(
        rule_ref=tricky,
        recommendation=tricky,
        evidence=_evidence(expected_acl_name=tricky, interface_name=tricky),
    )

    result = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    fetched = incidents.get_by_id(result.incident.incident_id)
    assert fetched is not None
    assert fetched.rule_ref == tricky
    assert fetched.recommendation == tricky
    assert fetched.evidence.expected_acl_name == tricky
    assert fetched.evidence.interface_name == tricky
