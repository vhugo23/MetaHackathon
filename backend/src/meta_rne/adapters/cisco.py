"""Cisco IOS-XE configuration adapter (architecture.md Section 5.1).

Line-oriented state machine (binding, per the approved Day 3A plan):

1. Blank lines are ignored.
2. ``!`` terminates the current block.
3. Any non-indented top-level line terminates the previous block *before*
   being interpreted itself.
4. A recognized top-level declaration establishes its own context
   (interface / named ACL / BGP).
5. An unrecognized but well-formed top-level line resets the context (via
   rule 3) and is otherwise ignored.
6. A child (indented) command is interpreted only while a compatible
   context is open; otherwise it is ignored.
7. ACL-reference validation runs once, after the full file is parsed, so
   an ACL may be declared after the interface that references it.

All parsing helpers below are private — the only public surface is
``CiscoAdapter.parse``.
"""

import ipaddress
import re
from dataclasses import dataclass, field

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
    VendorType,
)
from meta_rne.domain.errors import ParseError, ParseErrorCode

_HOSTNAME_RE = re.compile(r"^hostname\s+(\S+)\s*$")
_INTERFACE_OPEN_RE = re.compile(r"^interface\s+(\S+)\s*$")
_DESCRIPTION_RE = re.compile(r"^description\s+(.+?)\s*$")
_IP_ADDRESS_RE = re.compile(r"^ip address\s+(\S+)\s+(\S+)\s*$")
_ACCESS_GROUP_RE = re.compile(r"^ip access-group\s+(\S+)\s+(\S+)\s*$")
_ACL_NAMED_OPEN_RE = re.compile(r"^ip access-list\s+(?:standard|extended)\s+(\S+)\s*$")
_ACL_NUMBERED_RE = re.compile(r"^access-list\s+(\d+)\s+(permit|deny)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_ACL_NAMED_ENTRY_RE = re.compile(r"^(?:(\d+)\s+)?(permit|deny)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_ROUTER_BGP_OPEN_RE = re.compile(r"^router bgp\s+\d+\s*$")
_BGP_NEIGHBOR_RE = re.compile(r"^neighbor\s+(\S+)\s+remote-as\s+(\S+)\s*$")

_CONTEXT_NONE = "none"
_CONTEXT_INTERFACE = "interface"
_CONTEXT_ACL = "acl"
_CONTEXT_BGP = "bgp"


@dataclass
class _InterfaceBuilder:
    name: str
    description: str | None = None
    ip_address: str | None = None
    admin_state: AdminState = AdminState.UP
    acl_in: str | None = None
    acl_out: str | None = None


@dataclass
class _PendingAclEntry:
    sequence: int | None
    action: AclAction
    protocol: str
    source: str
    destination: str


@dataclass
class _AclBuilder:
    name: str
    entries: list[_PendingAclEntry] = field(default_factory=list)


def _mask_to_prefix_length(mask_text: str) -> int:
    """Raises ValueError for an unparsable or non-contiguous mask."""
    network = ipaddress.IPv4Network(f"0.0.0.0/{mask_text}", strict=False)
    return network.prefixlen


def _normalize_ip_and_mask(
    address_text: str, mask_text: str
) -> tuple[str | None, ParseErrorCode | None]:
    try:
        address = ipaddress.IPv4Address(address_text)
    except ValueError:
        return None, ParseErrorCode.INVALID_INTERFACE_IP
    try:
        prefix_length = _mask_to_prefix_length(mask_text)
    except ValueError:
        return None, ParseErrorCode.INVALID_SUBNET_MASK
    return f"{address}/{prefix_length}", None


def _try_normalize_bgp_neighbor(
    neighbor_ip_text: str, remote_as_text: str
) -> NormalizedBgpNeighbor | ParseErrorCode:
    """Returns the normalized neighbor, or the ParseErrorCode identifying
    why a line matching the recognized BGP neighbor command shape has an
    invalid value (architecture.md Section 5.1).
    """
    try:
        address = ipaddress.IPv4Address(neighbor_ip_text)
    except ValueError:
        return ParseErrorCode.INVALID_BGP_NEIGHBOR_IP
    try:
        remote_as = int(remote_as_text)
    except ValueError:
        return ParseErrorCode.INVALID_BGP_REMOTE_AS
    if remote_as <= 0:
        return ParseErrorCode.INVALID_BGP_REMOTE_AS
    return NormalizedBgpNeighbor(neighbor_ip=str(address), remote_as=remote_as)


def _finalize_acl_entries(pending: list[_PendingAclEntry]) -> tuple[NormalizedAclEntry, ...]:
    """Entries without an explicit sequence get one assigned in encounter
    order, skipping past any sequence (explicit or already-assigned) that
    would otherwise collide. Explicit sequences are kept as given. The
    result is ordered by final sequence — never reordered alphabetically
    by protocol/source/destination.
    """
    explicit_sequences = {entry.sequence for entry in pending if entry.sequence is not None}
    used_sequences = set(explicit_sequences)
    greatest_assigned = 0

    finalized: list[NormalizedAclEntry] = []
    for entry in pending:
        if entry.sequence is not None:
            sequence = entry.sequence
        else:
            candidate = (greatest_assigned // 10 + 1) * 10
            while candidate in used_sequences:
                candidate += 10
            sequence = candidate
            used_sequences.add(sequence)
        greatest_assigned = max(greatest_assigned, sequence)
        finalized.append(
            NormalizedAclEntry(
                sequence=sequence,
                action=entry.action,
                protocol=entry.protocol,
                source=entry.source,
                destination=entry.destination,
            )
        )
    return tuple(sorted(finalized, key=lambda e: e.sequence))


class CiscoAdapter:
    vendor_id: str = VendorType.CISCO_IOS_XE

    def parse(self, raw_text: str) -> NormalizedConfiguration | ParseError:
        if not raw_text.strip():
            return ParseError(
                code=ParseErrorCode.EMPTY_CONFIGURATION,
                message="configuration text is empty or whitespace-only",
            )

        hostname: str | None = None
        interfaces: dict[str, _InterfaceBuilder] = {}
        acls: dict[str, _AclBuilder] = {}
        bgp_neighbors: list[NormalizedBgpNeighbor] = []

        context = _CONTEXT_NONE
        current_interface: _InterfaceBuilder | None = None
        current_acl: _AclBuilder | None = None

        for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
            stripped = raw_line.strip()

            if not stripped:
                continue  # rule 1

            if stripped == "!":
                context = _CONTEXT_NONE
                current_interface = None
                current_acl = None
                continue  # rule 2

            is_top_level = raw_line == raw_line.lstrip()

            if is_top_level:
                # rule 3: terminate whatever block was open before
                # interpreting this line, unconditionally
                context = _CONTEXT_NONE
                current_interface = None
                current_acl = None

                if (match := _HOSTNAME_RE.match(stripped)) is not None:
                    hostname = match.group(1)
                    continue
                if stripped.startswith("hostname"):
                    return ParseError(
                        code=ParseErrorCode.MALFORMED_HOSTNAME,
                        message="hostname declaration has no single-token value",
                        line_number=line_number,
                        line=raw_line,
                    )

                if (match := _INTERFACE_OPEN_RE.match(stripped)) is not None:
                    name = match.group(1)
                    current_interface = interfaces.setdefault(name, _InterfaceBuilder(name=name))
                    context = _CONTEXT_INTERFACE
                    continue
                if stripped.startswith("interface"):
                    return ParseError(
                        code=ParseErrorCode.MALFORMED_INTERFACE,
                        message="interface declaration has no single-token name",
                        line_number=line_number,
                        line=raw_line,
                    )

                if (match := _ACL_NAMED_OPEN_RE.match(stripped)) is not None:
                    name = match.group(1)
                    current_acl = acls.setdefault(name, _AclBuilder(name=name))
                    context = _CONTEXT_ACL
                    continue

                if (match := _ACL_NUMBERED_RE.match(stripped)) is not None:
                    number, action, protocol, source, destination = match.groups()
                    acl = acls.setdefault(number, _AclBuilder(name=number))
                    acl.entries.append(
                        _PendingAclEntry(
                            sequence=None,
                            action=AclAction(action),
                            protocol=protocol,
                            source=source,
                            destination=destination,
                        )
                    )
                    continue

                if _ROUTER_BGP_OPEN_RE.match(stripped) is not None:
                    context = _CONTEXT_BGP
                    continue

                # rule 5: unrecognized top-level line — context already
                # reset above; ignored
                continue

            # indented (child) line — rule 6
            if context == _CONTEXT_INTERFACE and current_interface is not None:
                if (match := _DESCRIPTION_RE.match(stripped)) is not None:
                    current_interface.description = match.group(1)
                    continue
                if (match := _IP_ADDRESS_RE.match(stripped)) is not None:
                    address_text, mask_text = match.groups()
                    normalized_ip, error_code = _normalize_ip_and_mask(address_text, mask_text)
                    if error_code is not None:
                        return ParseError(
                            code=error_code,
                            message=f"invalid interface address/mask on line: {stripped!r}",
                            line_number=line_number,
                            line=raw_line,
                        )
                    current_interface.ip_address = normalized_ip
                    continue
                if stripped == "shutdown":
                    current_interface.admin_state = AdminState.DOWN
                    continue
                if stripped == "no shutdown":
                    current_interface.admin_state = AdminState.UP
                    continue
                if (match := _ACCESS_GROUP_RE.match(stripped)) is not None:
                    acl_name, direction_text = match.groups()
                    try:
                        direction = AclDirection(direction_text)
                    except ValueError:
                        return ParseError(
                            code=ParseErrorCode.INVALID_ACL_DIRECTION,
                            message=f"invalid ip access-group direction: {direction_text!r}",
                            line_number=line_number,
                            line=raw_line,
                        )
                    if direction == AclDirection.IN:
                        current_interface.acl_in = acl_name
                    else:
                        current_interface.acl_out = acl_name
                    continue
                continue  # unrecognized interface child command — ignored

            if context == _CONTEXT_ACL and current_acl is not None:
                if (match := _ACL_NAMED_ENTRY_RE.match(stripped)) is not None:
                    sequence_text, action, protocol, source, destination = match.groups()
                    current_acl.entries.append(
                        _PendingAclEntry(
                            sequence=int(sequence_text) if sequence_text is not None else None,
                            action=AclAction(action),
                            protocol=protocol,
                            source=source,
                            destination=destination,
                        )
                    )
                    continue
                continue  # unrecognized ACL child command — ignored

            if context == _CONTEXT_BGP:
                if (match := _BGP_NEIGHBOR_RE.match(stripped)) is not None:
                    neighbor_ip_text, remote_as_text = match.groups()
                    result = _try_normalize_bgp_neighbor(neighbor_ip_text, remote_as_text)
                    if isinstance(result, ParseErrorCode):
                        return ParseError(
                            code=result,
                            message=f"invalid BGP neighbor declaration on line: {stripped!r}",
                            line_number=line_number,
                            line=raw_line,
                        )
                    bgp_neighbors.append(result)
                    continue
                continue  # unrecognized BGP child command — ignored

            # no compatible open context — ignored
            continue

        if hostname is None:
            return ParseError(
                code=ParseErrorCode.MISSING_HOSTNAME,
                message="configuration has no hostname declaration",
            )

        # rule 7: ACL-reference validation after the full file is parsed
        for interface in interfaces.values():
            for acl_name in (interface.acl_in, interface.acl_out):
                if acl_name is not None and acl_name not in acls:
                    return ParseError(
                        code=ParseErrorCode.UNDECLARED_ACL_REFERENCE,
                        message=(
                            f"interface {interface.name!r} references "
                            f"undeclared ACL {acl_name!r}"
                        ),
                    )

        normalized_interfaces = tuple(
            sorted(
                (
                    NormalizedInterface(
                        name=iface.name,
                        description=iface.description,
                        ip_address=iface.ip_address,
                        mtu=None,  # mtu parsing is out of scope for Day 3A
                        admin_state=iface.admin_state,
                        acl_in=iface.acl_in,
                        acl_out=iface.acl_out,
                    )
                    for iface in interfaces.values()
                ),
                key=lambda iface: iface.name,
            )
        )

        normalized_acls = tuple(
            sorted(
                (
                    NormalizedAcl(name=acl.name, entries=_finalize_acl_entries(acl.entries))
                    for acl in acls.values()
                ),
                key=lambda acl: acl.name,
            )
        )

        normalized_bgp_neighbors = tuple(
            sorted(bgp_neighbors, key=lambda neighbor: ipaddress.IPv4Address(neighbor.neighbor_ip))
        )

        return NormalizedConfiguration(
            hostname=hostname,
            interfaces=normalized_interfaces,
            routing=NormalizedRouting(bgp_neighbors=normalized_bgp_neighbors),
            acls=normalized_acls,
        )
