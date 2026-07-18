"""Normalized, vendor-neutral configuration value objects.

Names are prefixed with ``Normalized`` to distinguish these transient,
in-memory values (produced fresh by a vendor adapter's ``parse()`` call)
from the persisted network entities a later day will add (e.g. a
``Device`` row) — see domain-model.md Sections 2-5.

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Everything here
is an immutable ``@dataclass(frozen=True, slots=True)`` using ``tuple``
for collections, per the Day 3A engineering constraints.

``static_routes`` is intentionally absent from ``NormalizedRouting`` for
Day 3A: static-route parsing is not implemented this phase, and an empty
field with no populating logic and no dedicated type would be exactly the
"shape completeness" abstraction Day 3A was told to avoid. See
docs/domain-model.md Section 5 for the full (eventual) Routing shape.
"""

from dataclasses import dataclass
from enum import StrEnum


class VendorType(StrEnum):
    """domain-model.md Section 16 — internal-only; never the type of the
    raw HTTP request field (product-spec.md FR-01/NFR-05)."""

    CISCO_IOS_XE = "cisco-ios-xe"
    ARISTA_EOS = "arista-eos"


class AdminState(StrEnum):
    UP = "up"
    DOWN = "down"


class AclAction(StrEnum):
    PERMIT = "permit"
    DENY = "deny"


class AclDirection(StrEnum):
    IN = "in"
    OUT = "out"


@dataclass(frozen=True, slots=True)
class NormalizedAclEntry:
    sequence: int
    action: AclAction
    protocol: str
    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class NormalizedAcl:
    name: str
    entries: tuple[NormalizedAclEntry, ...]


@dataclass(frozen=True, slots=True)
class NormalizedInterface:
    name: str
    description: str | None
    ip_address: str | None
    mtu: int | None
    admin_state: AdminState
    acl_in: str | None
    acl_out: str | None


@dataclass(frozen=True, slots=True)
class NormalizedBgpNeighbor:
    neighbor_ip: str
    remote_as: int


@dataclass(frozen=True, slots=True)
class NormalizedRouting:
    bgp_neighbors: tuple[NormalizedBgpNeighbor, ...]


@dataclass(frozen=True, slots=True)
class NormalizedConfiguration:
    hostname: str
    interfaces: tuple[NormalizedInterface, ...]
    routing: NormalizedRouting
    acls: tuple[NormalizedAcl, ...]
