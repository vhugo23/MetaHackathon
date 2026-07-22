"""Deterministic configuration drift detection (FR-04, AC-05/AC-06).

Pure domain/detection logic: plain `NormalizedConfiguration` inputs in, a
`DriftReport` out, no I/O, no clock access, no repository access, no
incident creation. See docs/architecture.md Section 8 for the approved
contract; see docs/domain-model.md's `DriftDetector` port signature.

Comparison walks each top-level collection that actually exists on
`NormalizedConfiguration` today — `interfaces`, `routing.bgp_neighbors`,
`acls` — keyed by natural identity (name / neighbor IP), diffing the
documented scalar fields within matches. `routing.static_routes` is named
in architecture.md's narrative but is not implemented on
`NormalizedRouting` yet (domain/config.py) and is therefore not compared;
`hostname` is a top-level scalar, not a collection, and is likewise not
part of this walk.

Resource strings, `changed`-entry field names, and whole-resource
added/removed value representation follow the conventions confirmed for
Gate 2 (no document defines them beyond the `{added, removed, changed}`
shape): `"interface:<name>"` / `"acl:<name>"` / `"bgp_neighbor:<neighbor_ip>"`
resources; `field` is the exact `NormalizedInterface`/`NormalizedBgpNeighbor`
attribute name for a `changed` entry, `None` for a whole-resource
`added`/`removed` entry; `old_value`/`new_value` on a whole-resource entry
is the resource's own identity string. Output preserves each input tuple's
order (never re-sorted), the same determinism precedent `PolicyEvaluator`
uses.

`NormalizedAcl.entries` is a nested collection, not a scalar field, and no
ACL-entry serialization contract is approved — a matched ACL (same name in
both inputs) never produces a `changed` entry, regardless of its `entries`
content; only whole-ACL addition/removal is compared.
"""

from enum import Enum

from meta_rne.domain.config import (
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
)
from meta_rne.domain.drift import DriftEntry, DriftReport


def _stringify(value: str | int | Enum | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _diff_interfaces(
    baseline: NormalizedConfiguration,
    current: NormalizedConfiguration,
    added: list[DriftEntry],
    removed: list[DriftEntry],
    changed: list[DriftEntry],
) -> None:
    baseline_by_name = {interface.name: interface for interface in baseline.interfaces}
    current_by_name = {interface.name: interface for interface in current.interfaces}

    for interface in baseline.interfaces:
        resource = f"interface:{interface.name}"
        current_interface = current_by_name.get(interface.name)
        if current_interface is None:
            removed.append(
                DriftEntry(resource=resource, field=None, old_value=interface.name, new_value=None)
            )
            continue
        changed.extend(_diff_interface_fields(resource, interface, current_interface))

    for interface in current.interfaces:
        if interface.name not in baseline_by_name:
            added.append(
                DriftEntry(
                    resource=f"interface:{interface.name}",
                    field=None,
                    old_value=None,
                    new_value=interface.name,
                )
            )


def _diff_interface_fields(
    resource: str, baseline: NormalizedInterface, current: NormalizedInterface
) -> list[DriftEntry]:
    entries: list[DriftEntry] = []
    fields: tuple[tuple[str, str | int | Enum | None, str | int | Enum | None], ...] = (
        ("description", baseline.description, current.description),
        ("ip_address", baseline.ip_address, current.ip_address),
        ("mtu", baseline.mtu, current.mtu),
        ("admin_state", baseline.admin_state, current.admin_state),
        ("acl_in", baseline.acl_in, current.acl_in),
        ("acl_out", baseline.acl_out, current.acl_out),
    )
    for field_name, old_value, new_value in fields:
        if old_value != new_value:
            entries.append(
                DriftEntry(
                    resource=resource,
                    field=field_name,
                    old_value=_stringify(old_value),
                    new_value=_stringify(new_value),
                )
            )
    return entries


def _diff_bgp_neighbors(
    baseline: NormalizedConfiguration,
    current: NormalizedConfiguration,
    added: list[DriftEntry],
    removed: list[DriftEntry],
    changed: list[DriftEntry],
) -> None:
    baseline_neighbors = baseline.routing.bgp_neighbors
    current_neighbors = current.routing.bgp_neighbors
    baseline_by_ip = {neighbor.neighbor_ip: neighbor for neighbor in baseline_neighbors}
    current_by_ip = {neighbor.neighbor_ip: neighbor for neighbor in current_neighbors}

    for neighbor in baseline_neighbors:
        resource = f"bgp_neighbor:{neighbor.neighbor_ip}"
        current_neighbor = current_by_ip.get(neighbor.neighbor_ip)
        if current_neighbor is None:
            removed.append(
                DriftEntry(
                    resource=resource, field=None, old_value=neighbor.neighbor_ip, new_value=None
                )
            )
            continue
        changed.extend(_diff_bgp_neighbor_fields(resource, neighbor, current_neighbor))

    for neighbor in current_neighbors:
        if neighbor.neighbor_ip not in baseline_by_ip:
            added.append(
                DriftEntry(
                    resource=f"bgp_neighbor:{neighbor.neighbor_ip}",
                    field=None,
                    old_value=None,
                    new_value=neighbor.neighbor_ip,
                )
            )


def _diff_bgp_neighbor_fields(
    resource: str, baseline: NormalizedBgpNeighbor, current: NormalizedBgpNeighbor
) -> list[DriftEntry]:
    if baseline.remote_as == current.remote_as:
        return []
    return [
        DriftEntry(
            resource=resource,
            field="remote_as",
            old_value=_stringify(baseline.remote_as),
            new_value=_stringify(current.remote_as),
        )
    ]


def _diff_acls(
    baseline: NormalizedConfiguration,
    current: NormalizedConfiguration,
    added: list[DriftEntry],
    removed: list[DriftEntry],
) -> None:
    """Whole-ACL identity comparison only.

    A matched ACL's `entries` collection is deliberately not compared —
    see the module docstring. This function therefore never appends to a
    `changed` list, unlike its interface/BGP-neighbor siblings, so it takes
    no `changed` parameter.
    """
    baseline_by_name = {acl.name: acl for acl in baseline.acls}
    current_by_name = {acl.name: acl for acl in current.acls}

    for acl in baseline.acls:
        if acl.name not in current_by_name:
            removed.append(
                DriftEntry(
                    resource=f"acl:{acl.name}", field=None, old_value=acl.name, new_value=None
                )
            )

    for acl in current.acls:
        if acl.name not in baseline_by_name:
            added.append(
                DriftEntry(
                    resource=f"acl:{acl.name}", field=None, old_value=None, new_value=acl.name
                )
            )


class DriftDetector:
    """Stateless; see docs/architecture.md Section 8."""

    @staticmethod
    def compare(baseline: NormalizedConfiguration, current: NormalizedConfiguration) -> DriftReport:
        added: list[DriftEntry] = []
        removed: list[DriftEntry] = []
        changed: list[DriftEntry] = []

        _diff_interfaces(baseline, current, added, removed, changed)
        _diff_bgp_neighbors(baseline, current, added, removed, changed)
        _diff_acls(baseline, current, added, removed)

        return DriftReport(added=tuple(added), removed=tuple(removed), changed=tuple(changed))
