import io
import json
from datetime import UTC, datetime

from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import (
    Incident,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.observability import IncidentLogEvent, StdoutIncidentEventSink

DEVICE_ID = "spine-01"
SOURCE = IncidentSource.POLICY_VIOLATION
RULE_REF = "policy-acl-external-in"
AFFECTED_RESOURCE = "interface:GigabitEthernet0/1:acl_in"
CREATED_AT = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
LAST_SEEN_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
FINGERPRINT = compute_fingerprint(DEVICE_ID, SOURCE, RULE_REF, AFFECTED_RESOURCE)

_REQUIRED_KEYS = (
    "incident_id",
    "device_id",
    "rule_ref",
    "severity",
    "status",
    "outcome",
    "timestamp",
)

_EXCLUDED_KEYS = (
    "source",
    "affected_resource",
    "evidence",
    "fingerprint",
    "recommendation",
    "occurrence_count",
    "created_at",
    "last_seen_at",
    "resolved_at",
)


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


def _incident(**overrides: object) -> Incident:
    defaults: dict[str, object] = {
        "incident_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "fingerprint": FINGERPRINT,
        "device_id": DEVICE_ID,
        "source": SOURCE,
        "rule_ref": RULE_REF,
        "affected_resource": AFFECTED_RESOURCE,
        "severity": Severity.MEDIUM,
        "status": IncidentStatus.OPEN,
        "evidence": _evidence(),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "created_at": CREATED_AT,
        "last_seen_at": LAST_SEEN_AT,
        "occurrence_count": 1,
        "updated_at": UPDATED_AT,
        "resolved_at": None,
    }
    defaults.update(overrides)
    return Incident(**defaults)  # type: ignore[arg-type]


def _result(outcome: IncidentUpsertOutcome, **incident_overrides: object) -> IncidentUpsertResult:
    return IncidentUpsertResult(incident=_incident(**incident_overrides), outcome=outcome)


# 1. Built from a CREATED result.
def test_from_upsert_result__created_outcome__builds_matching_event() -> None:
    result = _result(IncidentUpsertOutcome.CREATED)

    event = IncidentLogEvent.from_upsert_result(result)

    assert event.incident_id == result.incident.incident_id
    assert event.device_id == result.incident.device_id
    assert event.rule_ref == result.incident.rule_ref
    assert event.severity == result.incident.severity
    assert event.status == result.incident.status
    assert event.outcome == IncidentUpsertOutcome.CREATED


# 2. Built from an UPDATED result.
def test_from_upsert_result__updated_outcome__builds_matching_event() -> None:
    result = _result(IncidentUpsertOutcome.UPDATED)

    event = IncidentLogEvent.from_upsert_result(result)

    assert event.outcome == IncidentUpsertOutcome.UPDATED


# 3. Timestamp is exactly result.incident.last_seen_at.
def test_from_upsert_result__timestamp__is_exactly_incident_last_seen_at() -> None:
    distinct_last_seen_at = datetime(2026, 7, 18, 11, 30, 0, tzinfo=UTC)
    distinct_updated_at = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
    result = _result(
        IncidentUpsertOutcome.UPDATED,
        last_seen_at=distinct_last_seen_at,
        updated_at=distinct_updated_at,
    )

    event = IncidentLogEvent.from_upsert_result(result)

    assert event.timestamp == distinct_last_seen_at
    assert event.timestamp != result.incident.created_at
    assert event.timestamp != result.incident.updated_at


# 4. Serialization produces exactly the seven required keys.
def test_to_json__contains_exactly_the_seven_required_keys() -> None:
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    payload = json.loads(event.to_json())

    assert set(payload.keys()) == set(_REQUIRED_KEYS)


# 5. Enum values serialize using the existing public string values.
def test_to_json__enum_fields__use_public_string_values() -> None:
    event = IncidentLogEvent.from_upsert_result(
        _result(
            IncidentUpsertOutcome.UPDATED, severity=Severity.CRITICAL, status=IncidentStatus.OPEN
        )
    )

    payload = json.loads(event.to_json())

    assert payload["severity"] == "Critical"
    assert payload["status"] == "OPEN"
    assert payload["outcome"] == "UPDATED"


# 6. UTC timestamps serialize using the canonical trailing-Z representation.
def test_to_json__timestamp__uses_canonical_trailing_z_format() -> None:
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    payload = json.loads(event.to_json())

    assert payload["timestamp"] == "2026-07-18T10:00:00Z"
    assert "+00:00" not in payload["timestamp"]


# 7. Excluded fields never appear in the serialized JSON.
def test_to_json__excludes_undocumented_fields() -> None:
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    raw = event.to_json()
    payload = json.loads(raw)

    for excluded_key in _EXCLUDED_KEYS:
        assert excluded_key not in payload
        assert f'"{excluded_key}"' not in raw


# 8. Serialization is deterministic.
def test_to_json__called_twice__produces_identical_output() -> None:
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    assert event.to_json() == event.to_json()


# 4 (key order) — exact documented contract order.
def test_to_json__key_order__matches_documented_contract_order() -> None:
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    raw = event.to_json()
    parsed_in_order = list(json.loads(raw, object_pairs_hook=lambda pairs: pairs))

    assert [key for key, _value in parsed_in_order] == list(_REQUIRED_KEYS)


# 9. The stdout sink writes exactly one JSON object and one newline per event.
def test_stdout_sink__emit_one_event__writes_one_json_line() -> None:
    stream = io.StringIO()
    sink = StdoutIncidentEventSink(stream=stream)
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    sink.emit(event)

    written = stream.getvalue()
    assert written == event.to_json() + "\n"
    assert written.count("\n") == 1


# 10. The sink flushes the supplied stream after writing.
def test_stdout_sink__emit__flushes_the_supplied_stream() -> None:
    class _FlushTrackingStream(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.flush_count = 0

        def flush(self) -> None:
            self.flush_count += 1
            super().flush()

    stream = _FlushTrackingStream()
    sink = StdoutIncidentEventSink(stream=stream)
    event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))

    sink.emit(event)

    assert stream.flush_count == 1


# 11. The sink protocol can be satisfied by a recording double, no caplog/capsys.
def test_incident_event_sink_protocol__recording_double__records_emitted_events() -> None:
    class _RecordingSink:
        def __init__(self) -> None:
            self.calls: list[IncidentLogEvent] = []

        def emit(self, event: IncidentLogEvent) -> None:
            self.calls.append(event)

    sink = _RecordingSink()
    created_event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.CREATED))
    updated_event = IncidentLogEvent.from_upsert_result(_result(IncidentUpsertOutcome.UPDATED))

    sink.emit(created_event)
    sink.emit(updated_event)

    assert sink.calls == [created_event, updated_event]
