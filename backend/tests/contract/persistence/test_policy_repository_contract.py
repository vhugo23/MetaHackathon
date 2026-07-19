"""Repository conformance tests for ConfigurationPolicyRepository (Day 4B2).

Run against both the in-memory and SQLAlchemy implementations via the
shared ``repositories`` fixture (conftest.py in this directory).
Semantic-equivalence seeding: only ``applies_to``/``required_acls``
participate in conflict detection — ``created_at`` is insertion metadata
(Day 4B2 binding decision, CLAUDE.md "Current Phase"). Applicability
remains exact `applies_to == device_id` matching — no wildcard behavior.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 9, 30, 0, tzinfo=UTC)


def _acl_external_in_rule() -> RequiredAclRule:
    return RequiredAclRule(
        acl_name="ACL-EXTERNAL-IN",
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
        severity=Severity.MEDIUM,
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    )


def _policy(**overrides: object) -> ConfigurationPolicy:
    defaults: dict[str, object] = {
        "policy_id": "policy-acl-external-in",
        "applies_to": "spine-01",
        "required_acls": (_acl_external_in_rule(),),
        "created_at": T0,
    }
    defaults.update(overrides)
    return ConfigurationPolicy(**defaults)  # type: ignore[arg-type]


def test_policy_repository__no_match__returns_empty_tuple(repositories: SimpleNamespace) -> None:
    assert repositories.policies.get_applicable_to_device("spine-01") == ()


def test_policy_repository__slice1_seed__inserts(repositories: SimpleNamespace) -> None:
    policies = build_slice1_policies(created_at=T0)

    repositories.policies.seed_if_missing(policies)

    assert repositories.policies.get_applicable_to_device("spine-01") == policies


def test_policy_repository__exact_spine01_applicability(repositories: SimpleNamespace) -> None:
    repositories.policies.seed_if_missing((_policy(),))

    result = repositories.policies.get_applicable_to_device("spine-01")

    assert len(result) == 1
    assert result[0].policy_id == "policy-acl-external-in"


def test_policy_repository__not_returned_for_another_device(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(),))

    assert repositories.policies.get_applicable_to_device("leaf-01") == ()


def test_policy_repository__identical_semantic_seed__is_a_no_op(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(),))

    repositories.policies.seed_if_missing((_policy(),))

    assert repositories.policies.get_applicable_to_device("spine-01") == (_policy(),)


def test_policy_repository__different_created_at_identical_semantics__is_a_no_op(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(created_at=T0),))

    repositories.policies.seed_if_missing((_policy(created_at=T1),))

    result = repositories.policies.get_applicable_to_device("spine-01")
    assert len(result) == 1
    assert result[0].applies_to == "spine-01"
    assert result[0].required_acls == (_acl_external_in_rule(),)


def test_policy_repository__stored_created_at_remains_unchanged(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(created_at=T0),))

    repositories.policies.seed_if_missing((_policy(created_at=T1),))

    result = repositories.policies.get_applicable_to_device("spine-01")
    assert result[0].created_at == T0


def test_policy_repository__different_applies_to__raises_policy_seed_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(),))

    with pytest.raises(PolicySeedConflictError):
        repositories.policies.seed_if_missing((_policy(applies_to="leaf-01"),))


def test_policy_repository__different_rule_content__raises_policy_seed_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(),))
    different_rule = RequiredAclRule(
        acl_name="ACL-EXTERNAL-IN",
        interface_name="GigabitEthernet0/2",  # different interface
        direction=AclDirection.IN,
        severity=Severity.MEDIUM,
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/2",
    )

    with pytest.raises(PolicySeedConflictError):
        repositories.policies.seed_if_missing((_policy(required_acls=(different_rule,)),))


def test_policy_repository__conflict__leaves_stored_policy_unchanged(
    repositories: SimpleNamespace,
) -> None:
    original = _policy()
    repositories.policies.seed_if_missing((original,))

    try:
        repositories.policies.seed_if_missing((_policy(applies_to="leaf-01"),))
    except PolicySeedConflictError:
        pass

    assert repositories.policies.get_applicable_to_device("spine-01") == (original,)


def test_policy_repository__conflict_in_multi_policy_call__leaves_no_partial_new_seed(
    repositories: SimpleNamespace,
) -> None:
    other_policy = _policy(
        policy_id="policy-other",
        applies_to="leaf-01",
        required_acls=(_acl_external_in_rule(),),
    )
    conflicting_existing = _policy()
    repositories.policies.seed_if_missing((conflicting_existing,))

    conflicting_new = _policy(applies_to="leaf-02")
    try:
        repositories.policies.seed_if_missing((other_policy, conflicting_new))
    except PolicySeedConflictError:
        pass

    # other_policy must NOT have been inserted — the whole batch call failed atomically
    assert repositories.policies.get_applicable_to_device("leaf-01") == ()
    assert repositories.policies.get_applicable_to_device("spine-01") == (conflicting_existing,)


def test_policy_repository__required_acls_tuple_ordering_survives_persistence(
    repositories: SimpleNamespace,
) -> None:
    rule_a = RequiredAclRule(
        acl_name="ACL-A",
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
        severity=Severity.LOW,
        recommendation="Assign ACL-A inbound to GigabitEthernet0/1",
    )
    rule_b = RequiredAclRule(
        acl_name="ACL-B",
        interface_name="GigabitEthernet0/2",
        direction=AclDirection.OUT,
        severity=Severity.HIGH,
        recommendation="Assign ACL-B outbound to GigabitEthernet0/2",
    )
    policy = _policy(policy_id="policy-multi-rule", required_acls=(rule_a, rule_b))

    repositories.policies.seed_if_missing((policy,))

    result = repositories.policies.get_applicable_to_device("spine-01")
    assert result[0].required_acls == (rule_a, rule_b)


def test_policy_repository__returned_policies_ordered_by_policy_id(
    repositories: SimpleNamespace,
) -> None:
    policy_z = _policy(policy_id="policy-z", applies_to="spine-01")
    policy_a = _policy(policy_id="policy-a", applies_to="spine-01")
    repositories.policies.seed_if_missing((policy_z, policy_a))

    result = repositories.policies.get_applicable_to_device("spine-01")

    assert [p.policy_id for p in result] == ["policy-a", "policy-z"]


def test_policy_repository__returned_values_are_configuration_policy_not_orm_model(
    repositories: SimpleNamespace,
) -> None:
    repositories.policies.seed_if_missing((_policy(),))

    result = repositories.policies.get_applicable_to_device("spine-01")

    assert isinstance(result[0], ConfigurationPolicy)
