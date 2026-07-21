"""Unit tests for the Slice 1 + Day 8A-D policy seed builder.

``build_slice1_policies`` keeps its Day 4B2 name (accepted technical debt,
Gate 8A-D) while its deterministic return tuple has grown to two
device-specific policies: the original spine-01 (Cisco) policy, unchanged,
and a second, exact-match leaf-02 (Arista) policy expressing the same
logical required-ACL requirement — never a wildcard, never a shared
applicability mechanism.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 9, 30, 0, tzinfo=UTC)


def test_build_slice1_policies__returns_exactly_two_policies() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert len(policies) == 2


def test_build_slice1_policies__deterministic_order__spine_then_leaf() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert [p.policy_id for p in policies] == [
        "policy-acl-external-in",
        "policy-acl-external-in-leaf-02",
    ]


def test_build_slice1_policies__policy_ids_are_unique() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert len({p.policy_id for p in policies}) == len(policies)


def test_build_slice1_policies__applies_to_values_are_exact() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert [p.applies_to for p in policies] == ["spine-01", "leaf-02"]


def test_build_slice1_policies__spine01_policy_fields_unchanged() -> None:
    policies = build_slice1_policies(created_at=T0)
    spine_policy = policies[0]

    assert spine_policy.policy_id == "policy-acl-external-in"
    assert spine_policy.applies_to == "spine-01"
    assert spine_policy.created_at == T0
    assert len(spine_policy.required_acls) == 1
    rule = spine_policy.required_acls[0]
    assert rule.acl_name == "ACL-EXTERNAL-IN"
    assert rule.interface_name == "GigabitEthernet0/1"
    assert rule.direction == AclDirection.IN
    assert rule.severity == Severity.MEDIUM
    assert rule.recommendation == "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"


def test_build_slice1_policies__leaf02_policy_fields() -> None:
    policies = build_slice1_policies(created_at=T0)
    leaf_policy = policies[1]

    assert leaf_policy.policy_id == "policy-acl-external-in-leaf-02"
    assert leaf_policy.applies_to == "leaf-02"
    assert leaf_policy.created_at == T0
    assert len(leaf_policy.required_acls) == 1
    rule = leaf_policy.required_acls[0]
    assert rule.acl_name == "ACL-EXTERNAL-IN"
    assert rule.interface_name == "Ethernet1"
    assert rule.direction == AclDirection.IN
    assert rule.severity == Severity.MEDIUM
    assert rule.recommendation == "Assign ACL-EXTERNAL-IN inbound to Ethernet1"


def test_build_slice1_policies__same_created_at_twice__returns_equal_policies() -> None:
    assert build_slice1_policies(created_at=T0) == build_slice1_policies(created_at=T0)


def test_build_slice1_policies__both_policies_use_the_supplied_created_at() -> None:
    policies = build_slice1_policies(created_at=T0)

    assert all(policy.created_at == T0 for policy in policies)


def test_build_slice1_policies__naive_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        build_slice1_policies(created_at=datetime(2026, 7, 18, 9, 0, 0))


def test_build_slice1_policies__non_utc_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        build_slice1_policies(
            created_at=datetime(2026, 7, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        )


# --- Seeding both policies through the repository contract ------------------


def test_build_slice1_policies__seeded_into_empty_store__persists_both() -> None:
    store = InMemoryStore()
    repo = InMemoryConfigurationPolicyRepository(store)
    policies = build_slice1_policies(created_at=T0)

    repo.seed_if_missing(policies)

    spine_policies = repo.get_applicable_to_device("spine-01")
    leaf_policies = repo.get_applicable_to_device("leaf-02")
    assert spine_policies == (policies[0],)
    assert leaf_policies == (policies[1],)


def test_build_slice1_policies__repeated_seed__is_idempotent_and_creates_no_duplicates() -> None:
    store = InMemoryStore()
    repo = InMemoryConfigurationPolicyRepository(store)
    policies = build_slice1_policies(created_at=T0)

    repo.seed_if_missing(policies)
    repo.seed_if_missing(policies)

    assert len(repo.get_applicable_to_device("spine-01")) == 1
    assert len(repo.get_applicable_to_device("leaf-02")) == 1


def test_build_slice1_policies__semantically_equivalent_reseed__leaves_policies_unchanged() -> None:
    store = InMemoryStore()
    repo = InMemoryConfigurationPolicyRepository(store)
    repo.seed_if_missing(build_slice1_policies(created_at=T0))

    # A later call with a different created_at but identical
    # applies_to/required_acls content is a no-op — stored created_at (and
    # every other field) is left untouched.
    repo.seed_if_missing(build_slice1_policies(created_at=T1))

    spine_policies = repo.get_applicable_to_device("spine-01")
    leaf_policies = repo.get_applicable_to_device("leaf-02")
    assert spine_policies[0].created_at == T0
    assert leaf_policies[0].created_at == T0


def test_build_slice1_policies__same_policy_id_semantic_conflict__raises_deterministically() -> (
    None
):
    store = InMemoryStore()
    repo = InMemoryConfigurationPolicyRepository(store)
    repo.seed_if_missing(build_slice1_policies(created_at=T0))
    conflicting_leaf_policy = ConfigurationPolicy(
        policy_id="policy-acl-external-in-leaf-02",
        applies_to="a-different-device",
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-OTHER",
                interface_name="Ethernet2",
                direction=AclDirection.OUT,
                severity=Severity.LOW,
                recommendation="irrelevant",
            ),
        ),
        created_at=T0,
    )

    with pytest.raises(PolicySeedConflictError):
        repo.seed_if_missing((conflicting_leaf_policy,))

    # The conflict left the originally-seeded leaf-02 policy unchanged.
    leaf_policies = repo.get_applicable_to_device("leaf-02")
    assert leaf_policies[0].policy_id == "policy-acl-external-in-leaf-02"
    assert leaf_policies[0].required_acls[0].acl_name == "ACL-EXTERNAL-IN"
