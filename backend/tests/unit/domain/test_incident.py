import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType

DEVICE_ID = "spine-01"
SOURCE = IncidentSource.POLICY_VIOLATION
RULE_REF = "policy-acl-external-in"
AFFECTED_RESOURCE = "interface:GigabitEthernet0/1:acl_in"
OBSERVED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _evidence(**overrides: object) -> PolicyViolationIncidentEvidence:
    defaults: dict[str, object] = {
        "source_snapshot_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
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
        "source": SOURCE,
        "rule_ref": RULE_REF,
        "affected_resource": AFFECTED_RESOURCE,
        "severity": Severity.MEDIUM,
        "evidence": _evidence(),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "observed_at": OBSERVED_AT,
    }
    defaults.update(overrides)
    return IncidentCandidate(**defaults)  # type: ignore[arg-type]


def test_incident_status__has_approved_members_only() -> None:
    assert {member.value for member in IncidentStatus} == {"OPEN", "ACKNOWLEDGED", "RESOLVED"}


def test_incident_candidate__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(device_id="")


def test_incident_candidate__whitespace_only_rule_ref__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(rule_ref="   ")


def test_incident_candidate__empty_affected_resource__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(affected_resource="")


def test_incident_candidate__empty_recommendation__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(recommendation="")


def test_incident_candidate__naive_observed_at__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(observed_at=datetime(2026, 7, 18, 10, 0, 0))


def test_incident_candidate__non_utc_offset_observed_at__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _candidate(observed_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))))


def test_policy_violation_incident_evidence__empty_source_snapshot_id__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _evidence(source_snapshot_id="")


def test_compute_fingerprint__valid_inputs__returns_lowercase_64_char_hex_digest() -> None:
    fingerprint = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)

    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    assert all(char in "0123456789abcdef" for char in fingerprint)


def test_compute_fingerprint__identical_ordered_values__produce_same_fingerprint() -> None:
    first = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)
    second = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)

    assert first == second


def test_compute_fingerprint__changing_device_id__changes_fingerprint() -> None:
    baseline = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)
    changed = compute_fingerprint("leaf-01", SOURCE, RULE_REF, AFFECTED_RESOURCE)

    assert baseline != changed


def test_compute_fingerprint__changing_source__changes_fingerprint() -> None:
    baseline = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)
    changed = compute_fingerprint(DEVICE_ID, IncidentSource.ANOMALY, RULE_REF, AFFECTED_RESOURCE)

    assert baseline != changed


def test_compute_fingerprint__changing_rule_ref__changes_fingerprint() -> None:
    baseline = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)
    changed = compute_fingerprint(DEVICE_ID, SOURCE, "policy-other", AFFECTED_RESOURCE)

    assert baseline != changed


def test_compute_fingerprint__changing_affected_resource__changes_fingerprint() -> None:
    baseline = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)
    changed = compute_fingerprint(
        DEVICE_ID, SOURCE, RULE_REF, "interface:GigabitEthernet0/2:acl_in"
    )

    assert baseline != changed


def test_compute_fingerprint__delimiter_quote_escape_and_unicode_values__remain_unambiguous() -> (
    None
):
    # Naive "|"-joins collide when a delimiter appears inside one field vs.
    # split across two fields: "a|b" + "c" vs "a" + "b|c".
    pipe_in_first = compute_fingerprint("a|b", SOURCE, "c", AFFECTED_RESOURCE)
    pipe_split_across = compute_fingerprint("a", SOURCE, "b|c", AFFECTED_RESOURCE)
    assert pipe_in_first != pipe_split_across

    colon_in_resource = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "a:b")
    colon_split = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "a" + ":" + "b")
    # Same literal value both ways is fine; prove a genuinely different
    # colon placement still differs.
    colon_shifted = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "ab:")
    assert colon_in_resource == colon_split
    assert colon_in_resource != colon_shifted

    quote_value = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, 'interface:"weird":acl_in')
    backslash_value = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "interface:\\weird\\:acl_in")
    assert quote_value != backslash_value
    assert quote_value != AFFECTED_RESOURCE

    unicode_value = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "interface:Gi0/1-é:acl_in")
    ascii_value = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "interface:Gi0/1-e:acl_in")
    assert unicode_value != ascii_value


def test_compute_fingerprint__canonical_serialization_order_is_fixed() -> None:
    expected_payload = json.dumps(
        [DEVICE_ID, SOURCE.value, RULE_REF, AFFECTED_RESOURCE],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected_digest = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    result = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)

    assert result == expected_digest


def test_compute_fingerprint__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError):
        compute_fingerprint("", SOURCE, RULE_REF, AFFECTED_RESOURCE)


def test_compute_fingerprint__whitespace_only_rule_ref__raises_value_error() -> None:
    with pytest.raises(ValueError):
        compute_fingerprint(DEVICE_ID, SOURCE, "   ", AFFECTED_RESOURCE)


def test_compute_fingerprint__empty_affected_resource__raises_value_error() -> None:
    with pytest.raises(ValueError):
        compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, "")


# --- Persisted Incident (Day 4B1) -------------------------------------------

_FINGERPRINT = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)


def _incident(**overrides: object) -> Incident:
    defaults: dict[str, object] = {
        "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "fingerprint": _FINGERPRINT,
        "device_id": DEVICE_ID,
        "source": SOURCE,
        "rule_ref": RULE_REF,
        "affected_resource": AFFECTED_RESOURCE,
        "severity": Severity.MEDIUM,
        "status": IncidentStatus.OPEN,
        "evidence": _evidence(),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "created_at": OBSERVED_AT,
        "last_seen_at": OBSERVED_AT,
        "occurrence_count": 1,
    }
    defaults.update(overrides)
    return Incident(**defaults)  # type: ignore[arg-type]


def test_incident__valid_fields__constructs_successfully() -> None:
    incident = _incident()

    assert incident.fingerprint == _FINGERPRINT
    assert incident.occurrence_count == 1
    assert incident.status is IncidentStatus.OPEN


def test_incident__empty_incident_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="incident_id"):
        _incident(incident_id="")


def test_incident__fingerprint_wrong_length__raises_value_error() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        _incident(fingerprint="ab" * 10)


def test_incident__uppercase_fingerprint__raises_value_error() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        _incident(fingerprint=_FINGERPRINT.upper())


def test_incident__non_hex_fingerprint__raises_value_error() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        _incident(fingerprint="g" * 64)


def test_incident__occurrence_count_below_one__raises_value_error() -> None:
    with pytest.raises(ValueError, match="occurrence_count"):
        _incident(occurrence_count=0)


def test_incident__last_seen_at_before_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="last_seen_at"):
        _incident(created_at=OBSERVED_AT, last_seen_at=OBSERVED_AT - timedelta(seconds=1))


def test_incident__naive_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        _incident(created_at=datetime(2026, 7, 18, 10, 0, 0))


def test_incident_upsert_outcome__has_approved_members_only() -> None:
    assert {member.value for member in IncidentUpsertOutcome} == {"CREATED", "UPDATED"}


def test_incident_upsert_result__wraps_incident_and_outcome() -> None:
    incident = _incident()

    result = IncidentUpsertResult(incident=incident, outcome=IncidentUpsertOutcome.CREATED)

    assert result.incident is incident
    assert result.outcome is IncidentUpsertOutcome.CREATED
