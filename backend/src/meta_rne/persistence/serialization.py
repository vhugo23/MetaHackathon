"""Explicit JSON (de)serialization for persistence-facing domain values.

No pickle. Enums round-trip through their ``.value``; tuples round-trip
through JSON arrays, preserving order. Every ``_from_json`` function raises
exactly one exception type, ``SerializationError``, for any malformed input —
callers never see a leaked ``KeyError``/``TypeError``/``ValueError``/
``AttributeError`` from a stored structure that doesn't match the expected
shape (Day 4B1 binding decision, CLAUDE.md "Current Phase").

Day 4B1 scope: only ``IncidentSource.POLICY_VIOLATION`` evidence
(``PolicyViolationIncidentEvidence``) was supported. Gate H1 (Day 9c) adds
explicit, discriminated serialization for the three ``RuleEvidence``
subtypes (``CpuHighEvidence``/``LinkFlapEvidence``/``BgpDownEvidence``) —
three explicit per-type function pairs plus two small dispatch functions
(``anomaly_evidence_to_json``/``anomaly_evidence_from_json``), never a
generic recursive serializer, mirroring ``domain/anomaly.py``'s own
``_EVIDENCE_TYPE_BY_RULE_ID`` table. These functions are not yet called from
any repository or application code (Gate H1 scope is evidence-type widening
and serialization only). Drift evidence formats remain deferred.
"""

from datetime import UTC, datetime
from typing import Any

from meta_rne.domain.anomaly import (
    BgpDownEvidence,
    CpuHighEvidence,
    CpuSampleEvidence,
    InterfaceTransitionEvidence,
    LinkFlapEvidence,
    RuleEvidence,
    RuleId,
)
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
from meta_rne.domain.telemetry import BgpSession, BgpState, InterfaceState, LinkState


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


def _utc_datetime_from_json(value: Any, what: str) -> datetime:
    # No existing production precedent for datetime-in-JSON exists elsewhere
    # in this module — datetime.fromisoformat() is the plain stdlib
    # round-trip counterpart to datetime.isoformat() used on the to_json
    # side, imposing no invented textual format. A naive or non-UTC-offset
    # result is rejected here (never left for the constructed domain
    # object's own __post_init__ to reject as a raw ValueError).
    if not isinstance(value, str):
        raise SerializationError(f"{what} must be a string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SerializationError(f"{what} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SerializationError(f"{what} must be timezone-aware, got a naive timestamp: {value!r}")
    if parsed.utcoffset() != UTC.utcoffset(None):
        raise SerializationError(f"{what} must be UTC, got offset {parsed.utcoffset()}: {value!r}")
    return parsed


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


def interface_states_to_json(states: tuple[InterfaceState, ...]) -> list[dict[str, Any]]:
    return [{"name": state.name, "oper_state": state.oper_state.value} for state in states]


def interface_states_from_json(data: Any) -> tuple[InterfaceState, ...]:
    items = _require_list(data, "InterfaceState list")
    try:
        return tuple(
            InterfaceState(
                name=_get(item, "name", "InterfaceState"),
                oper_state=_enum(
                    LinkState, _get(item, "oper_state", "InterfaceState"), "oper_state"
                ),
            )
            for item in (_require_dict(item, "InterfaceState") for item in items)
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed InterfaceState: {exc}") from exc


def bgp_sessions_to_json(sessions: tuple[BgpSession, ...]) -> list[dict[str, Any]]:
    return [
        {"neighbor_ip": session.neighbor_ip, "state": session.state.value} for session in sessions
    ]


def bgp_sessions_from_json(data: Any) -> tuple[BgpSession, ...]:
    items = _require_list(data, "BgpSession list")
    try:
        return tuple(
            BgpSession(
                neighbor_ip=_get(item, "neighbor_ip", "BgpSession"),
                state=_enum(BgpState, _get(item, "state", "BgpSession"), "state"),
            )
            for item in (_require_dict(item, "BgpSession") for item in items)
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed BgpSession: {exc}") from exc


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


# --- Anomaly evidence (Gate H1) ----------------------------------------------
#
# Three explicit per-type function pairs, mirroring every other serializer in
# this module — never a generic recursive serializer. cpu_high_evidence_
# from_json/link_flap_evidence_from_json additionally catch ValueError (never
# caught by this module's other _from_json functions): CpuHighEvidence's/
# LinkFlapEvidence's own __post_init__ already enforces UTC-awareness on each
# nested timestamp (domain/anomaly.py's _require_utc), raising a plain
# ValueError — that must be translated to SerializationError here like every
# other checked exception, never left to leak raw.


def cpu_high_evidence_to_json(evidence: CpuHighEvidence) -> dict[str, Any]:
    return {
        "samples": [
            {
                "timestamp": sample.timestamp.isoformat(),
                "cpu_utilization_pct": sample.cpu_utilization_pct,
            }
            for sample in evidence.samples
        ]
    }


def cpu_high_evidence_from_json(data: Any) -> CpuHighEvidence:
    data = _require_dict(data, "CpuHighEvidence")
    try:
        return CpuHighEvidence(
            samples=tuple(
                CpuSampleEvidence(
                    timestamp=_utc_datetime_from_json(
                        _get(item, "timestamp", "CpuSampleEvidence"), "CpuSampleEvidence.timestamp"
                    ),
                    cpu_utilization_pct=_require_float(
                        _get(item, "cpu_utilization_pct", "CpuSampleEvidence"),
                        "CpuSampleEvidence.cpu_utilization_pct",
                    ),
                )
                for item in (
                    _require_dict(item, "CpuSampleEvidence")
                    for item in _require_list(_get(data, "samples", "CpuHighEvidence"), "samples")
                )
            )
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise SerializationError(f"malformed CpuHighEvidence: {exc}") from exc


def link_flap_evidence_to_json(evidence: LinkFlapEvidence) -> dict[str, Any]:
    return {
        "interface_name": evidence.interface_name,
        "transitions": [
            {
                "timestamp": transition.timestamp.isoformat(),
                "oper_state": transition.oper_state.value,
            }
            for transition in evidence.transitions
        ],
    }


def link_flap_evidence_from_json(data: Any) -> LinkFlapEvidence:
    data = _require_dict(data, "LinkFlapEvidence")
    try:
        return LinkFlapEvidence(
            interface_name=_get(data, "interface_name", "LinkFlapEvidence"),
            transitions=tuple(
                InterfaceTransitionEvidence(
                    timestamp=_utc_datetime_from_json(
                        _get(item, "timestamp", "InterfaceTransitionEvidence"),
                        "InterfaceTransitionEvidence.timestamp",
                    ),
                    oper_state=_enum(
                        LinkState,
                        _get(item, "oper_state", "InterfaceTransitionEvidence"),
                        "oper_state",
                    ),
                )
                for item in (
                    _require_dict(item, "InterfaceTransitionEvidence")
                    for item in _require_list(
                        _get(data, "transitions", "LinkFlapEvidence"), "transitions"
                    )
                )
            ),
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise SerializationError(f"malformed LinkFlapEvidence: {exc}") from exc


def bgp_down_evidence_to_json(evidence: BgpDownEvidence) -> dict[str, Any]:
    return {
        "neighbor_ip": evidence.neighbor_ip,
        "state": evidence.state.value,
        "previous_state": evidence.previous_state.value,
    }


def bgp_down_evidence_from_json(data: Any) -> BgpDownEvidence:
    data = _require_dict(data, "BgpDownEvidence")
    try:
        return BgpDownEvidence(
            neighbor_ip=_get(data, "neighbor_ip", "BgpDownEvidence"),
            state=_enum(BgpState, _get(data, "state", "BgpDownEvidence"), "state"),
            previous_state=_enum(
                BgpState, _get(data, "previous_state", "BgpDownEvidence"), "previous_state"
            ),
        )
    except SerializationError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SerializationError(f"malformed BgpDownEvidence: {exc}") from exc


_ANOMALY_EVIDENCE_TO_JSON_BY_TYPE: dict[type, Any] = {
    CpuHighEvidence: cpu_high_evidence_to_json,
    LinkFlapEvidence: link_flap_evidence_to_json,
    BgpDownEvidence: bgp_down_evidence_to_json,
}

_ANOMALY_EVIDENCE_FROM_JSON_BY_RULE_ID: dict[RuleId, Any] = {
    RuleId.CPU_HIGH: cpu_high_evidence_from_json,
    RuleId.LINK_FLAP: link_flap_evidence_from_json,
    RuleId.BGP_DOWN: bgp_down_evidence_from_json,
}


def anomaly_evidence_to_json(evidence: RuleEvidence) -> dict[str, Any]:
    try:
        serializer = _ANOMALY_EVIDENCE_TO_JSON_BY_TYPE[type(evidence)]
    except KeyError as exc:
        raise SerializationError(
            f"unsupported anomaly evidence type: {type(evidence).__name__}"
        ) from exc
    result: dict[str, Any] = serializer(evidence)
    return result


def anomaly_evidence_from_json(rule_ref: str, data: Any) -> RuleEvidence:
    rule_id = _enum(RuleId, rule_ref, "rule_ref")
    deserializer = _ANOMALY_EVIDENCE_FROM_JSON_BY_RULE_ID[rule_id]
    result: RuleEvidence = deserializer(data)
    return result


def _require_float(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SerializationError(f"{what} must be a number, got {type(value).__name__}")
    return float(value)
