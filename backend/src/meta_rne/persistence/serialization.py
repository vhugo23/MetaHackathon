"""Explicit JSON (de)serialization for persistence-facing domain values.

No pickle. Enums round-trip through their ``.value``; tuples round-trip
through JSON arrays, preserving order. Every ``_from_json`` function raises
exactly one exception type, ``SerializationError``, for any malformed input —
callers never see a leaked ``KeyError``/``TypeError``/``ValueError``/
``AttributeError`` from a stored structure that doesn't match the expected
shape (Day 4B1 binding decision, CLAUDE.md "Current Phase").

Day 4B1 scope: only ``IncidentSource.POLICY_VIOLATION`` evidence
(``PolicyViolationIncidentEvidence``) is supported. Drift/anomaly evidence
formats are deferred.
"""

from typing import Any

from meta_rne.domain.config import (
    AclAction,
    AclDirection,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)
from meta_rne.domain.incident import PolicyViolationIncidentEvidence
from meta_rne.domain.policy import RequiredAclRule, Severity, ViolationType


class SerializationError(Exception):
    """Raised by any ``_from_json`` function in this module for malformed,
    incomplete, or otherwise unsupported stored data — the one stable
    exception type persistence callers need to handle."""


def _require_dict(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SerializationError(f"{what} must be a JSON object, got {type(data).__name__}")
    return data


def _require_list(data: Any, what: str) -> list[Any]:
    if not isinstance(data, list):
        raise SerializationError(f"{what} must be a JSON array, got {type(data).__name__}")
    return data


def _get(data: dict[str, Any], key: str, what: str) -> Any:
    try:
        return data[key]
    except KeyError as exc:
        raise SerializationError(f"{what} is missing required key {key!r}") from exc


def _enum(enum_cls: type, value: Any, what: str) -> Any:
    try:
        return enum_cls(value)
    except (ValueError, TypeError) as exc:
        raise SerializationError(f"{what} has an unsupported value: {value!r}") from exc


# --- NormalizedConfiguration ------------------------------------------------


def normalized_config_to_json(config: NormalizedConfiguration) -> dict[str, Any]:
    return {
        "hostname": config.hostname,
        "interfaces": [
            {
                "name": interface.name,
                "description": interface.description,
                "ip_address": interface.ip_address,
                "mtu": interface.mtu,
                "admin_state": interface.admin_state.value,
                "acl_in": interface.acl_in,
                "acl_out": interface.acl_out,
            }
            for interface in config.interfaces
        ],
        "routing": {
            "bgp_neighbors": [
                {"neighbor_ip": neighbor.neighbor_ip, "remote_as": neighbor.remote_as}
                for neighbor in config.routing.bgp_neighbors
            ],
        },
        "acls": [
            {
                "name": acl.name,
                "entries": [
                    {
                        "sequence": entry.sequence,
                        "action": entry.action.value,
                        "protocol": entry.protocol,
                        "source": entry.source,
                        "destination": entry.destination,
                    }
                    for entry in acl.entries
                ],
            }
            for acl in config.acls
        ],
    }


def normalized_config_from_json(data: Any) -> NormalizedConfiguration:
    data = _require_dict(data, "NormalizedConfiguration")
    try:
        interfaces = tuple(
            NormalizedInterface(
                name=_get(item, "name", "NormalizedInterface"),
                description=_get(item, "description", "NormalizedInterface"),
                ip_address=_get(item, "ip_address", "NormalizedInterface"),
                mtu=_get(item, "mtu", "NormalizedInterface"),
                admin_state=_enum(
                    AdminState, _get(item, "admin_state", "NormalizedInterface"), "admin_state"
                ),
                acl_in=_get(item, "acl_in", "NormalizedInterface"),
                acl_out=_get(item, "acl_out", "NormalizedInterface"),
            )
            for item in _require_list(
                _get(data, "interfaces", "NormalizedConfiguration"), "interfaces"
            )
        )

        routing_data = _require_dict(_get(data, "routing", "NormalizedConfiguration"), "routing")
        bgp_neighbors = tuple(
            NormalizedBgpNeighbor(
                neighbor_ip=_get(item, "neighbor_ip", "NormalizedBgpNeighbor"),
                remote_as=_get(item, "remote_as", "NormalizedBgpNeighbor"),
            )
            for item in _require_list(
                _get(routing_data, "bgp_neighbors", "routing"), "bgp_neighbors"
            )
        )

        acls = tuple(
            NormalizedAcl(
                name=_get(item, "name", "NormalizedAcl"),
                entries=tuple(
                    NormalizedAclEntry(
                        sequence=_get(entry, "sequence", "NormalizedAclEntry"),
                        action=_enum(
                            AclAction, _get(entry, "action", "NormalizedAclEntry"), "action"
                        ),
                        protocol=_get(entry, "protocol", "NormalizedAclEntry"),
                        source=_get(entry, "source", "NormalizedAclEntry"),
                        destination=_get(entry, "destination", "NormalizedAclEntry"),
                    )
                    for entry in _require_list(_get(item, "entries", "NormalizedAcl"), "entries")
                ),
            )
            for item in _require_list(_get(data, "acls", "NormalizedConfiguration"), "acls")
        )

        return NormalizedConfiguration(
            hostname=_get(data, "hostname", "NormalizedConfiguration"),
            interfaces=interfaces,
            routing=NormalizedRouting(bgp_neighbors=bgp_neighbors),
            acls=acls,
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed NormalizedConfiguration: {exc}") from exc


# --- RequiredAclRule tuples --------------------------------------------------


def required_acl_rules_to_json(rules: tuple[RequiredAclRule, ...]) -> list[dict[str, Any]]:
    return [
        {
            "acl_name": rule.acl_name,
            "interface_name": rule.interface_name,
            "direction": rule.direction.value,
            "severity": rule.severity.value,
            "recommendation": rule.recommendation,
        }
        for rule in rules
    ]


def required_acl_rules_from_json(data: Any) -> tuple[RequiredAclRule, ...]:
    items = _require_list(data, "RequiredAclRule list")
    try:
        return tuple(
            RequiredAclRule(
                acl_name=_get(item, "acl_name", "RequiredAclRule"),
                interface_name=_get(item, "interface_name", "RequiredAclRule"),
                direction=_enum(
                    AclDirection, _get(item, "direction", "RequiredAclRule"), "direction"
                ),
                severity=_enum(Severity, _get(item, "severity", "RequiredAclRule"), "severity"),
                recommendation=_get(item, "recommendation", "RequiredAclRule"),
            )
            for item in (_require_dict(item, "RequiredAclRule") for item in items)
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed RequiredAclRule: {exc}") from exc


# --- PolicyViolationIncidentEvidence -----------------------------------------


def policy_violation_evidence_to_json(evidence: PolicyViolationIncidentEvidence) -> dict[str, Any]:
    return {
        "source_snapshot_id": evidence.source_snapshot_id,
        "violation_type": evidence.violation_type.value,
        "expected_acl_name": evidence.expected_acl_name,
        "actual_acl_name": evidence.actual_acl_name,
        "interface_name": evidence.interface_name,
        "direction": evidence.direction.value,
    }


def policy_violation_evidence_from_json(data: Any) -> PolicyViolationIncidentEvidence:
    data = _require_dict(data, "PolicyViolationIncidentEvidence")
    try:
        return PolicyViolationIncidentEvidence(
            source_snapshot_id=_get(data, "source_snapshot_id", "PolicyViolationIncidentEvidence"),
            violation_type=_enum(
                ViolationType,
                _get(data, "violation_type", "PolicyViolationIncidentEvidence"),
                "violation_type",
            ),
            expected_acl_name=_get(data, "expected_acl_name", "PolicyViolationIncidentEvidence"),
            actual_acl_name=_get(data, "actual_acl_name", "PolicyViolationIncidentEvidence"),
            interface_name=_get(data, "interface_name", "PolicyViolationIncidentEvidence"),
            direction=_enum(
                AclDirection,
                _get(data, "direction", "PolicyViolationIncidentEvidence"),
                "direction",
            ),
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed PolicyViolationIncidentEvidence: {exc}") from exc
