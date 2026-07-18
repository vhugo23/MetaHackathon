"""Deterministic configuration policy evaluation (FR-03).

Pure domain/detection logic: plain inputs in, a tuple out, no I/O, no
clock access, no repository access. See docs/architecture.md Section 7
and docs/domain-model.md Section 7 for the approved contract.
"""

from datetime import UTC, datetime

from meta_rne.domain.config import AclDirection, NormalizedConfiguration, NormalizedInterface
from meta_rne.domain.policy import (
    AclAssignmentEvidence,
    ConfigurationPolicy,
    ConfigurationViolation,
    RequiredAclRule,
    ViolationType,
)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _find_interface(
    config: NormalizedConfiguration, interface_name: str
) -> NormalizedInterface | None:
    for interface in config.interfaces:
        if interface.name == interface_name:
            return interface
    return None


def _actual_acl_name(interface: NormalizedInterface, rule: RequiredAclRule) -> str | None:
    if rule.direction == AclDirection.IN:
        return interface.acl_in
    return interface.acl_out


def _affected_resource(rule: RequiredAclRule) -> str:
    slot = "acl_in" if rule.direction == AclDirection.IN else "acl_out"
    return f"interface:{rule.interface_name}:{slot}"


def _evaluate_rule(
    device_id: str,
    source_snapshot_id: str,
    observed_at: datetime,
    config: NormalizedConfiguration,
    policy: ConfigurationPolicy,
    rule: RequiredAclRule,
) -> ConfigurationViolation | None:
    interface = _find_interface(config, rule.interface_name)
    affected_resource = _affected_resource(rule)

    if interface is None:
        violation_type = ViolationType.TARGET_INTERFACE_MISSING
        actual_acl_name = None
    else:
        actual_acl_name = _actual_acl_name(interface, rule)
        if actual_acl_name == rule.acl_name:
            return None
        violation_type = ViolationType.MISSING_REQUIRED_ACL

    return ConfigurationViolation(
        device_id=device_id,
        source_snapshot_id=source_snapshot_id,
        rule_ref=policy.policy_id,
        violation_type=violation_type,
        affected_resource=affected_resource,
        severity=rule.severity,
        evidence=AclAssignmentEvidence(
            expected_acl_name=rule.acl_name,
            actual_acl_name=actual_acl_name,
            interface_name=rule.interface_name,
            direction=rule.direction,
        ),
        recommendation=rule.recommendation,
        detected_at=observed_at,
    )


class PolicyEvaluator:
    """Stateless; see docs/domain-model.md Section 17."""

    @staticmethod
    def evaluate(
        device_id: str,
        source_snapshot_id: str,
        observed_at: datetime,
        config: NormalizedConfiguration,
        policies: tuple[ConfigurationPolicy, ...],
    ) -> tuple[ConfigurationViolation, ...]:
        _require_non_empty(device_id, "device_id")
        _require_non_empty(source_snapshot_id, "source_snapshot_id")
        _require_utc(observed_at, "observed_at")

        violations: list[ConfigurationViolation] = []
        for policy in policies:
            if policy.applies_to != device_id:
                continue
            for rule in policy.required_acls:
                violation = _evaluate_rule(
                    device_id,
                    source_snapshot_id,
                    observed_at,
                    config,
                    policy,
                    rule,
                )
                if violation is not None:
                    violations.append(violation)

        return tuple(violations)
