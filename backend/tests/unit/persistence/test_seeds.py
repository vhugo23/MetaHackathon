"""Unit tests for the pure Slice 1 policy seed builder (Day 4B2)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import Severity
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)


def test_build_slice1_policies__returns_exactly_one_policy() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert len(policies) == 1


def test_build_slice1_policies__policy_id_and_applicability() -> None:
    (policy,) = build_slice1_policies(created_at=T0)

    assert policy.policy_id == "policy-acl-external-in"
    assert policy.applies_to == "spine-01"
    assert policy.created_at == T0


def test_build_slice1_policies__required_acl_rule_fields() -> None:
    (policy,) = build_slice1_policies(created_at=T0)

    assert len(policy.required_acls) == 1
    rule = policy.required_acls[0]
    assert rule.acl_name == "ACL-EXTERNAL-IN"
    assert rule.interface_name == "GigabitEthernet0/1"
    assert rule.direction == AclDirection.IN
    assert rule.severity == Severity.MEDIUM
    assert rule.recommendation == "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"


def test_build_slice1_policies__same_created_at_twice__returns_equal_policies() -> None:
    assert build_slice1_policies(created_at=T0) == build_slice1_policies(created_at=T0)


def test_build_slice1_policies__naive_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        build_slice1_policies(created_at=datetime(2026, 7, 18, 9, 0, 0))


def test_build_slice1_policies__non_utc_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        build_slice1_policies(
            created_at=datetime(2026, 7, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        )
