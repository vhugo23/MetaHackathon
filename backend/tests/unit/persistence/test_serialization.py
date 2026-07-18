"""Explicit JSON serialization tests for persistence-facing domain values.

See docs/domain-model.md Sections 5-7 for the shapes being round-tripped
and Day 4B1's binding decision on serialization (CLAUDE.md "Current Phase"):
no pickle, enums preserved by value, tuple ordering preserved, malformed
stored structures rejected via one stable ``SerializationError`` rather than
a leaked ``KeyError``/``TypeError``/``ValueError``/``AttributeError``.
"""

from meta_rne.domain import (
    AclAction,
    AclDirection,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
    PolicyViolationIncidentEvidence,
    RequiredAclRule,
    Severity,
    ViolationType,
)
from meta_rne.persistence.serialization import (
    SerializationError,
    normalized_config_from_json,
    normalized_config_to_json,
    policy_violation_evidence_from_json,
    policy_violation_evidence_to_json,
    required_acl_rules_from_json,
    required_acl_rules_to_json,
)


def _sample_normalized_config() -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname="spine-01",
        interfaces=(
            NormalizedInterface(
                name="GigabitEthernet0/1",
                description=None,
                ip_address="10.0.0.1/30",
                mtu=1500,
                admin_state=AdminState.UP,
                acl_in="ACL-EXTERNAL-IN",
                acl_out=None,
            ),
        ),
        routing=NormalizedRouting(
            bgp_neighbors=(NormalizedBgpNeighbor(neighbor_ip="10.0.0.2", remote_as=65001),)
        ),
        acls=(
            NormalizedAcl(
                name="ACL-EXTERNAL-IN",
                entries=(
                    NormalizedAclEntry(
                        sequence=10,
                        action=AclAction.PERMIT,
                        protocol="tcp",
                        source="any",
                        destination="any",
                    ),
                ),
            ),
        ),
    )


def test_normalized_config_to_json_then_from_json__returns_equal_config() -> None:
    config = _sample_normalized_config()

    round_tripped = normalized_config_from_json(normalized_config_to_json(config))

    assert round_tripped == config


def test_normalized_config_from_json__wrong_top_level_type__raises_serialization_error() -> None:
    try:
        normalized_config_from_json([])  # type: ignore[arg-type]
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_normalized_config_from_json__missing_required_key__raises_serialization_error() -> None:
    data = normalized_config_to_json(_sample_normalized_config())
    del data["hostname"]

    try:
        normalized_config_from_json(data)
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_normalized_config_from_json__unknown_enum_value__raises_serialization_error() -> None:
    data = normalized_config_to_json(_sample_normalized_config())
    data["interfaces"][0]["admin_state"] = "sideways"

    try:
        normalized_config_from_json(data)
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_normalized_config_from_json__malformed_nested_collection__raises_serialization_error() -> (
    None
):
    data = normalized_config_to_json(_sample_normalized_config())
    data["interfaces"] = "not-a-list"

    try:
        normalized_config_from_json(data)
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_required_acl_rules_to_json_then_from_json__preserves_tuple_order_and_enum_values() -> None:
    rules = (
        RequiredAclRule(
            acl_name="ACL-EXTERNAL-IN",
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
            severity=Severity.MEDIUM,
            recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        ),
        RequiredAclRule(
            acl_name="ACL-EXTERNAL-OUT",
            interface_name="GigabitEthernet0/2",
            direction=AclDirection.OUT,
            severity=Severity.LOW,
            recommendation="Assign ACL-EXTERNAL-OUT outbound to GigabitEthernet0/2",
        ),
    )

    round_tripped = required_acl_rules_from_json(required_acl_rules_to_json(rules))

    assert round_tripped == rules
    assert isinstance(round_tripped, tuple)


def test_required_acl_rules_from_json__malformed_structure__raises_serialization_error() -> None:
    try:
        required_acl_rules_from_json("not-a-list")  # type: ignore[arg-type]
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_required_acl_rules_from_json__missing_required_key__raises_serialization_error() -> None:
    rule = RequiredAclRule(
        acl_name="ACL-EXTERNAL-IN",
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
        severity=Severity.MEDIUM,
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    )
    data = required_acl_rules_to_json((rule,))
    del data[0]["acl_name"]

    try:
        required_acl_rules_from_json(data)
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_policy_violation_evidence_to_json_then_from_json__returns_equal_evidence() -> None:
    evidence = PolicyViolationIncidentEvidence(
        source_snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        violation_type=ViolationType.MISSING_REQUIRED_ACL,
        expected_acl_name="ACL-EXTERNAL-IN",
        actual_acl_name=None,
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
    )

    round_tripped = policy_violation_evidence_from_json(policy_violation_evidence_to_json(evidence))

    assert round_tripped == evidence


def test_policy_violation_evidence_from_json__malformed_evidence__raises_serialization_error() -> (
    None
):
    evidence = PolicyViolationIncidentEvidence(
        source_snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        violation_type=ViolationType.MISSING_REQUIRED_ACL,
        expected_acl_name="ACL-EXTERNAL-IN",
        actual_acl_name=None,
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
    )
    data = policy_violation_evidence_to_json(evidence)
    data["violation_type"] = "NOT_A_REAL_TYPE"

    try:
        policy_violation_evidence_from_json(data)
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass


def test_policy_violation_evidence_from_json__wrong_top_level_type__raises_error() -> None:
    try:
        policy_violation_evidence_from_json(None)  # type: ignore[arg-type]
        raise AssertionError("expected SerializationError")
    except SerializationError:
        pass
