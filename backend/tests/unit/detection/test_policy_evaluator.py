from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.detection.policy_evaluator import PolicyEvaluator
from meta_rne.domain.config import (
    AclDirection,
    AdminState,
    NormalizedAcl,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity, ViolationType

DEVICE_ID = "spine-01"
SNAPSHOT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
OBSERVED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)

INTERFACE_NAME = "GigabitEthernet0/1"
REQUIRED_ACL_NAME = "ACL-EXTERNAL-IN"
RECOMMENDATION = "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"


def _interface(**overrides: object) -> NormalizedInterface:
    defaults: dict[str, object] = {
        "name": INTERFACE_NAME,
        "description": None,
        "ip_address": "10.0.0.1/30",
        "mtu": None,
        "admin_state": AdminState.UP,
        "acl_in": None,
        "acl_out": None,
    }
    defaults.update(overrides)
    return NormalizedInterface(**defaults)  # type: ignore[arg-type]


def _config(
    interfaces: tuple[NormalizedInterface, ...] = (),
    acls: tuple[NormalizedAcl, ...] = (),
) -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=DEVICE_ID,
        interfaces=interfaces,
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=acls,
    )


def _rule(**overrides: object) -> RequiredAclRule:
    defaults: dict[str, object] = {
        "acl_name": REQUIRED_ACL_NAME,
        "interface_name": INTERFACE_NAME,
        "direction": AclDirection.IN,
        "severity": Severity.MEDIUM,
        "recommendation": RECOMMENDATION,
    }
    defaults.update(overrides)
    return RequiredAclRule(**defaults)  # type: ignore[arg-type]


def _policy(
    policy_id: str = "policy-acl-external-in",
    applies_to: str = DEVICE_ID,
    required_acls: tuple[RequiredAclRule, ...] = (),
) -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id=policy_id,
        applies_to=applies_to,
        required_acls=required_acls or (_rule(),),
        created_at=OBSERVED_AT,
    )


def test_policy_evaluator__acl_assigned_correctly__no_violations() -> None:
    config = _config(interfaces=(_interface(acl_in=REQUIRED_ACL_NAME),))
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result == ()


def test_policy_evaluator__policy_for_other_device__is_ignored() -> None:
    config = _config(interfaces=())
    policies = (_policy(applies_to="leaf-01"),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result == ()


def test_policy_evaluator__no_matching_policy__no_violations() -> None:
    config = _config(interfaces=())

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, ())

    assert result == ()


def test_policy_evaluator__target_interface_missing__one_target_interface_missing_violation() -> (
    None
):
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert len(result) == 1
    assert result[0].violation_type == ViolationType.TARGET_INTERFACE_MISSING
    assert result[0].evidence.actual_acl_name is None


def test_policy_evaluator__acl_entirely_absent__one_missing_required_acl_violation() -> None:
    config = _config(interfaces=(_interface(),))
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert len(result) == 1
    assert result[0].violation_type == ViolationType.MISSING_REQUIRED_ACL
    assert result[0].evidence.actual_acl_name is None


def test_policy_evaluator__acl_present_but_unassigned__one_missing_required_acl_violation() -> None:
    config = _config(
        interfaces=(_interface(),),
        acls=(NormalizedAcl(name=REQUIRED_ACL_NAME, entries=()),),
    )
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert len(result) == 1
    assert result[0].violation_type == ViolationType.MISSING_REQUIRED_ACL
    assert result[0].evidence.actual_acl_name is None


def test_policy_evaluator__different_acl_assigned__violation_evidence_has_actual_acl_name() -> None:
    config = _config(interfaces=(_interface(acl_in="ACL-OTHER"),))
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert len(result) == 1
    assert result[0].violation_type == ViolationType.MISSING_REQUIRED_ACL
    assert result[0].evidence.actual_acl_name == "ACL-OTHER"


def test_policy_evaluator__outbound_rule_satisfied__no_violations() -> None:
    config = _config(interfaces=(_interface(acl_out=REQUIRED_ACL_NAME),))
    policies = (_policy(required_acls=(_rule(direction=AclDirection.OUT),)),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result == ()


def test_policy_evaluator__violation__contains_device_id() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].device_id == DEVICE_ID


def test_policy_evaluator__violation__contains_source_snapshot_id() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].source_snapshot_id == SNAPSHOT_ID


def test_policy_evaluator__violation__contains_rule_ref() -> None:
    config = _config(interfaces=())
    policies = (_policy(policy_id="policy-acl-external-in"),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].rule_ref == "policy-acl-external-in"


def test_policy_evaluator__violation__contains_affected_resource() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].affected_resource == f"interface:{INTERFACE_NAME}:acl_in"


def test_policy_evaluator__violation__contains_medium_severity() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].severity == Severity.MEDIUM


def test_policy_evaluator__violation__evidence_describes_expected_and_actual_state() -> None:
    config = _config(interfaces=(_interface(acl_in="ACL-OTHER"),))
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    evidence = result[0].evidence
    assert evidence.expected_acl_name == REQUIRED_ACL_NAME
    assert evidence.actual_acl_name == "ACL-OTHER"
    assert evidence.interface_name == INTERFACE_NAME
    assert evidence.direction == AclDirection.IN


def test_policy_evaluator__violation__contains_recommendation() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert result[0].recommendation == RECOMMENDATION


def test_policy_evaluator__given_observed_at__populates_violation_detected_at() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)
    observed_at = datetime(2026, 7, 18, 12, 30, 0, tzinfo=UTC)

    result = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, observed_at, config, policies)

    assert result[0].detected_at == observed_at


def test_policy_evaluator__identical_inputs__produce_equal_violations() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    first = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)
    second = PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert first == second


def test_policy_evaluator__multiple_applicable_policies__evaluated_independently() -> None:
    config = _config(interfaces=())
    policy_a = _policy(
        policy_id="policy-a",
        required_acls=(_rule(interface_name="GigabitEthernet0/1"),),
    )
    policy_b = _policy(
        policy_id="policy-b",
        required_acls=(_rule(interface_name="GigabitEthernet0/2"),),
    )

    result = PolicyEvaluator.evaluate(
        DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, (policy_a, policy_b)
    )

    assert [violation.rule_ref for violation in result] == ["policy-a", "policy-b"]


def test_policy_evaluator__evaluate__does_not_mutate_config_or_policies() -> None:
    config = _config(interfaces=(_interface(),))
    policies = (_policy(),)
    config_before = config
    policies_before = policies

    PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, OBSERVED_AT, config, policies)

    assert config == config_before
    assert policies == policies_before


def test_policy_evaluator__naive_observed_at__raises_value_error() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)
    naive_observed_at = datetime(2026, 7, 18, 10, 0, 0)

    with pytest.raises(ValueError):
        PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, naive_observed_at, config, policies)


def test_policy_evaluator__non_utc_observed_at__raises_value_error() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)
    non_utc_observed_at = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError):
        PolicyEvaluator.evaluate(DEVICE_ID, SNAPSHOT_ID, non_utc_observed_at, config, policies)


def test_policy_evaluator__empty_device_id__raises_value_error() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    with pytest.raises(ValueError):
        PolicyEvaluator.evaluate("", SNAPSHOT_ID, OBSERVED_AT, config, policies)


def test_policy_evaluator__whitespace_only_source_snapshot_id__raises_value_error() -> None:
    config = _config(interfaces=())
    policies = (_policy(),)

    with pytest.raises(ValueError):
        PolicyEvaluator.evaluate(DEVICE_ID, "   ", OBSERVED_AT, config, policies)
