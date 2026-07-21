"""Arista EOS adapter contract tests (Gate 8A-A).

Binding per the approved Day 8A parser-contract plan: these tests define
the Arista EOS subset ``AristaAdapter.parse`` must implement in Gate 8A-B.
Every test that actually calls ``parse()`` is expected to be RED this gate
— ``AristaAdapter.parse`` deliberately raises ``NotImplementedError`` (see
``meta_rne.adapters.arista``) until Gate 8A-B. Only
``test_arista_adapter__vendor_id__is_arista_eos`` is expected to pass.

Fixture-path/naming/assertion conventions follow
``tests/unit/adapters/test_cisco_adapter.py`` — not mechanically copied,
since Arista's supported subset (named-only ACLs, CIDR addressing, no
numbered-ACL syntax) is narrower than Cisco's.
"""

from pathlib import Path

import pytest

from meta_rne.adapters.arista import AristaAdapter
from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.domain.config import (
    AclAction,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)
from meta_rne.domain.errors import ParseError, ParseErrorCode, UnsupportedVendorError

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "configs" / "arista"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Identity and successful normalization
# ---------------------------------------------------------------------------


def test_arista_adapter__vendor_id__is_arista_eos() -> None:
    assert AristaAdapter().vendor_id == "arista-eos"


def test_adapter_registry__cisco_only__rejects_arista_eos() -> None:
    """Production composition is not changing until Gate 8A-C — this proves
    the registry's existing rejection behavior for "arista-eos" is
    unaffected by this gate's skeleton, using a registry built directly
    from CiscoAdapter only (never build_production_adapter_registry, and
    never AristaAdapter), matching the existing
    test_adapter_registry__resolve_arista__raises_unsupported_vendor_error
    convention in test_adapter_registry.py."""
    registry = AdapterRegistry([CiscoAdapter()])

    with pytest.raises(UnsupportedVendorError) as exc_info:
        registry.resolve("arista-eos")

    assert exc_info.value.vendor == "arista-eos"


def test_arista_adapter__required_acl_assigned_fixture__returns_normalized_config() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert result == NormalizedConfiguration(
        hostname="leaf-02",
        interfaces=(
            NormalizedInterface(
                name="Ethernet1",
                description="Uplink to spine-01",
                ip_address="10.0.1.1/30",
                mtu=None,
                admin_state=AdminState.UP,
                acl_in="ACL-EXTERNAL-IN",
                acl_out="ACL-EXTERNAL-OUT",
            ),
            NormalizedInterface(
                name="Ethernet2",
                description="Downlink to leaf-03",
                ip_address=None,
                mtu=None,
                admin_state=AdminState.DOWN,
                acl_in=None,
                acl_out=None,
            ),
        ),
        routing=NormalizedRouting(
            bgp_neighbors=(NormalizedBgpNeighbor(neighbor_ip="10.0.1.2", remote_as=65001),)
        ),
        acls=(
            NormalizedAcl(
                name="ACL-EXTERNAL-IN",
                entries=(
                    NormalizedAclEntry(
                        sequence=10,
                        action=AclAction.PERMIT,
                        protocol="ip",
                        source="any",
                        destination="any",
                    ),
                    NormalizedAclEntry(
                        sequence=20,
                        action=AclAction.DENY,
                        protocol="ip",
                        source="any",
                        destination="any",
                    ),
                ),
            ),
            NormalizedAcl(
                name="ACL-EXTERNAL-OUT",
                entries=(
                    NormalizedAclEntry(
                        sequence=10,
                        action=AclAction.PERMIT,
                        protocol="ip",
                        source="any",
                        destination="any",
                    ),
                ),
            ),
        ),
    )


def test_arista_adapter__missing_required_acl_fixture__returns_normalized_config() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_missing_required_acl.txt"))

    assert result == NormalizedConfiguration(
        hostname="leaf-02",
        interfaces=(
            NormalizedInterface(
                name="Ethernet1",
                description="Uplink to spine-01",
                ip_address="10.0.1.1/30",
                mtu=None,
                admin_state=AdminState.UP,
                acl_in=None,
                acl_out=None,
            ),
        ),
        routing=NormalizedRouting(
            bgp_neighbors=(NormalizedBgpNeighbor(neighbor_ip="10.0.1.2", remote_as=65001),)
        ),
        acls=(),
    )


def test_arista_adapter__same_input_parsed_twice__returns_equal_normalized_config() -> None:
    text = _load_fixture("arista_required_acl_assigned.txt")
    adapter = AristaAdapter()

    assert adapter.parse(text) == adapter.parse(text)


def test_arista_adapter__parse__does_not_mutate_input_text() -> None:
    text = _load_fixture("arista_required_acl_assigned.txt")
    original = text

    AristaAdapter().parse(text)

    assert text == original


# ---------------------------------------------------------------------------
# Interface behavior
# ---------------------------------------------------------------------------


def test_arista_adapter__cidr_ip_address__normalizes_address_and_prefix_length() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    eth1 = next(i for i in result.interfaces if i.name == "Ethernet1")
    assert eth1.ip_address == "10.0.1.1/30"


def test_arista_adapter__shutdown__normalizes_admin_state_down() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    eth2 = next(i for i in result.interfaces if i.name == "Ethernet2")
    assert eth2.admin_state == AdminState.DOWN


def test_arista_adapter__no_shutdown_or_absent__normalizes_admin_state_up() -> None:
    absent_result = AristaAdapter().parse("hostname leaf-02\ninterface Ethernet1\n")
    assert isinstance(absent_result, NormalizedConfiguration)
    assert absent_result.interfaces[0].admin_state == AdminState.UP

    explicit_result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))
    assert isinstance(explicit_result, NormalizedConfiguration)
    eth1 = next(i for i in explicit_result.interfaces if i.name == "Ethernet1")
    assert eth1.admin_state == AdminState.UP


def test_arista_adapter__inbound_access_group__normalizes_acl_in() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    eth1 = next(i for i in result.interfaces if i.name == "Ethernet1")
    assert eth1.acl_in == "ACL-EXTERNAL-IN"


def test_arista_adapter__outbound_access_group__normalizes_acl_out() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    eth1 = next(i for i in result.interfaces if i.name == "Ethernet1")
    assert eth1.acl_out == "ACL-EXTERNAL-OUT"


def test_arista_adapter__duplicate_interface_declaration__reopens_and_merges() -> None:
    text = (
        "hostname leaf-02\n"
        "interface Ethernet1\n"
        "   ip address 10.0.1.1/30\n"
        "!\n"
        "interface Ethernet1\n"
        "   description reopened\n"
        "!\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert len(result.interfaces) == 1
    eth1 = result.interfaces[0]
    # Reopening the same interface name merges into the same builder — the
    # earlier block's field is not cleared by the later block, matching the
    # existing Cisco precedent (dict.setdefault reopens the same object).
    assert eth1.ip_address == "10.0.1.1/30"
    assert eth1.description == "reopened"


# ---------------------------------------------------------------------------
# ACL behavior
# ---------------------------------------------------------------------------


def test_arista_adapter__acl_declaration__normalizes_acl_entries() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    acl_in = next(a for a in result.acls if a.name == "ACL-EXTERNAL-IN")
    assert acl_in.entries == (
        NormalizedAclEntry(
            sequence=10, action=AclAction.PERMIT, protocol="ip", source="any", destination="any"
        ),
        NormalizedAclEntry(
            sequence=20, action=AclAction.DENY, protocol="ip", source="any", destination="any"
        ),
    )


def test_arista_adapter__mixed_explicit_and_implicit_acl_sequences__remain_unique() -> None:
    text = (
        "hostname leaf-02\n"
        "ip access-list ACL-MIXED\n"
        "   10 permit ip any any\n"
        "   deny ip any any\n"
        "   permit ip any any\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    acl = next(a for a in result.acls if a.name == "ACL-MIXED")
    assert [e.sequence for e in acl.entries] == [10, 20, 30]


def test_arista_adapter__implicit_sequences__assigned_in_encounter_order() -> None:
    text = (
        "hostname leaf-02\n"
        "ip access-list ACL-UNNUMBERED\n"
        "   permit tcp any any\n"
        "   deny udp any any\n"
        "   permit ip any any\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    acl = next(a for a in result.acls if a.name == "ACL-UNNUMBERED")
    # Encounter order is preserved by protocol, not just by the resulting
    # sequence numbers, proving entries are never reordered by content.
    assert [(e.sequence, e.protocol) for e in acl.entries] == [
        (10, "tcp"),
        (20, "udp"),
        (30, "ip"),
    ]


def test_arista_adapter__high_explicit_sequence__never_raises_later_implicit_candidates() -> None:
    """A single test proving the full binding sequence-assignment contract:
    explicit sequences are collected from the whole ACL before any implicit
    assignment happens (so the explicit 100 and 20 below are both known
    before the first implicit candidate is chosen, even though 100 is
    encountered after the first implicit entry); candidate selection always
    restarts at 10 (never anchored to the highest sequence seen so far, so
    the high explicit 100 never pushes the second implicit entry's
    candidate above the still-free 30); occupied 20 is skipped; implicit
    entries are assigned in encounter order; and the normalized result is
    never re-sorted numerically by sequence."""
    text = (
        "hostname leaf-02\n"
        "ip access-list ACL-SEQUENCE-GAP\n"
        "   permit tcp any any\n"
        "   100 deny udp any any\n"
        "   20 permit icmp any any\n"
        "   deny ip any any\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    acl = next(a for a in result.acls if a.name == "ACL-SEQUENCE-GAP")
    # Encounter order, identified by protocol, proves entries are never
    # re-sorted by their resulting sequence number.
    assert [(e.sequence, e.protocol) for e in acl.entries] == [
        (10, "tcp"),
        (100, "udp"),
        (20, "icmp"),
        (30, "ip"),
    ]


def test_arista_adapter__duplicate_acl_declaration__reopens_and_merges() -> None:
    text = (
        "hostname leaf-02\n"
        "ip access-list ACL-DUP\n"
        "   10 permit ip any any\n"
        "!\n"
        "ip access-list ACL-DUP\n"
        "   20 deny ip any any\n"
        "!\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    matching = [a for a in result.acls if a.name == "ACL-DUP"]
    assert len(matching) == 1
    assert [e.sequence for e in matching[0].entries] == [10, 20]


def test_arista_adapter__acl_reference_before_definition__is_valid() -> None:
    text = (
        "hostname leaf-02\n"
        "interface Ethernet1\n"
        "   ip access-group ACL-LATER in\n"
        "!\n"
        "ip access-list ACL-LATER\n"
        "   permit ip any any\n"
        "!\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    eth1 = result.interfaces[0]
    assert eth1.acl_in == "ACL-LATER"


# ---------------------------------------------------------------------------
# BGP behavior
# ---------------------------------------------------------------------------


def test_arista_adapter__bgp_neighbor__normalizes_remote_as() -> None:
    result = AristaAdapter().parse(_load_fixture("arista_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    assert result.routing.bgp_neighbors == (
        NormalizedBgpNeighbor(neighbor_ip="10.0.1.2", remote_as=65001),
    )


def test_arista_adapter__multiple_bgp_neighbors__deterministic_order() -> None:
    text = (
        "hostname leaf-02\n"
        "router bgp 65002\n"
        "   neighbor 10.0.1.10 remote-as 65010\n"
        "   neighbor 10.0.1.2 remote-as 65001\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    # Numeric IPv4 order, not lexicographic: "10.0.1.2" sorts before
    # "10.0.1.10" numerically but AFTER it lexicographically.
    assert [n.neighbor_ip for n in result.routing.bgp_neighbors] == [
        "10.0.1.2",
        "10.0.1.10",
    ]


# ---------------------------------------------------------------------------
# Ignored syntax
# ---------------------------------------------------------------------------


def test_arista_adapter__unknown_top_level_command__is_ignored_not_rejected() -> None:
    text = (
        "hostname leaf-02\n"
        "interface Ethernet1\n"
        "   ip address 10.0.1.1/30\n"
        "!\n"
        "spanning-tree mode mstp\n"
        "   ip address 10.0.1.99/30\n"
        "!\n"
        "interface Ethernet2\n"
        "   ip address 10.0.1.5/30\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert [i.name for i in result.interfaces] == ["Ethernet1", "Ethernet2"]
    eth1 = result.interfaces[0]
    # If the unknown top-level command had NOT reset the context, the
    # indented "ip address 10.0.1.99/30" line right after it would have
    # been (incorrectly) applied to the still-open Ethernet1 block instead
    # of being ignored.
    assert eth1.ip_address == "10.0.1.1/30"


def test_arista_adapter__unknown_child_command__is_ignored_not_rejected() -> None:
    text = (
        "hostname leaf-02\n"
        "interface Ethernet1\n"
        "   switchport mode access\n"
        "   ip address 10.0.1.1/30\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert result.interfaces[0].ip_address == "10.0.1.1/30"


def test_arista_adapter__unknown_mtu_command__is_ignored_not_rejected() -> None:
    """MTU remains unsupported this slice (domain-model.md's documented
    gap, mirrored from Cisco) — an `mtu` line is unknown-but-well-formed,
    never a new parser error, and never populates `Interface.mtu`."""
    text = "hostname leaf-02\ninterface Ethernet1\n   mtu 9214\n   ip address 10.0.1.1/30\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert result.interfaces[0].mtu is None


def test_arista_adapter__blank_lines_and_separators__are_ignored() -> None:
    text = (
        "\n"
        "hostname leaf-02\n"
        "\n"
        "!\n"
        "interface Ethernet1\n"
        "\n"
        "   ip address 10.0.1.1/30\n"
        "!\n"
        "\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert result.hostname == "leaf-02"
    assert result.interfaces[0].ip_address == "10.0.1.1/30"


def test_arista_adapter__eof_without_trailing_separator__finalizes_open_block() -> None:
    text = "hostname leaf-02\ninterface Ethernet1\n   ip address 10.0.1.1/30\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert result.interfaces[0].ip_address == "10.0.1.1/30"


def test_arista_adapter__duplicate_hostname__later_declaration_wins() -> None:
    result = AristaAdapter().parse("hostname leaf-01\nhostname leaf-02\n")

    assert isinstance(result, NormalizedConfiguration)
    assert result.hostname == "leaf-02"


# ---------------------------------------------------------------------------
# Returned ParseError cases
# ---------------------------------------------------------------------------


def test_arista_adapter__empty_input__returns_parse_error() -> None:
    result = AristaAdapter().parse("")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.EMPTY_CONFIGURATION
    assert "empty" in result.message.lower()
    assert result.line_number is None


def test_arista_adapter__whitespace_only_input__returns_parse_error() -> None:
    result = AristaAdapter().parse("   \n\t\n  ")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.EMPTY_CONFIGURATION
    assert "empty" in result.message.lower()
    assert result.line_number is None


def test_arista_adapter__missing_hostname__returns_parse_error() -> None:
    text = "interface Ethernet1\n   ip address 10.0.1.1/30\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MISSING_HOSTNAME
    assert "hostname" in result.message.lower()
    assert result.line_number is None


def test_arista_adapter__malformed_hostname_declaration__returns_parse_error() -> None:
    result = AristaAdapter().parse("hostname\n")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MALFORMED_HOSTNAME
    assert "hostname" in result.message.lower()
    assert result.line_number == 1
    assert result.line is not None and "hostname" in result.line


def test_arista_adapter__malformed_interface_declaration__returns_parse_error() -> None:
    text = "hostname leaf-02\ninterface\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MALFORMED_INTERFACE
    assert "interface" in result.message.lower()
    assert result.line_number == 2
    assert result.line is not None and "interface" in result.line


def test_arista_adapter__invalid_cidr_ip_address__returns_parse_error() -> None:
    text = "hostname leaf-02\ninterface Ethernet1\n   ip address 999.0.1.1/30\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_INTERFACE_IP
    assert "address" in result.message.lower()
    assert result.line_number == 3
    assert result.line is not None and "999.0.1.1" in result.line


def test_arista_adapter__invalid_cidr_prefix_length__returns_parse_error() -> None:
    text = "hostname leaf-02\ninterface Ethernet1\n   ip address 10.0.1.1/33\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_SUBNET_MASK
    assert result.line_number == 3
    assert result.line is not None and "10.0.1.1/33" in result.line


def test_arista_adapter__invalid_acl_direction__returns_parse_error() -> None:
    text = (
        "hostname leaf-02\n"
        "interface Ethernet1\n"
        "   ip access-group ACL-EXTERNAL-IN sideways\n"
        "!\n"
        "ip access-list ACL-EXTERNAL-IN\n"
        "   permit ip any any\n"
    )

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_ACL_DIRECTION
    assert "direction" in result.message.lower()
    assert result.line_number == 3
    assert result.line is not None and "sideways" in result.line


def test_arista_adapter__acl_assignment_references_undeclared_acl__returns_parse_error() -> None:
    text = "hostname leaf-02\ninterface Ethernet1\n   ip access-group ACL-DOES-NOT-EXIST in\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.UNDECLARED_ACL_REFERENCE
    assert "ACL-DOES-NOT-EXIST" in result.message
    assert "Ethernet1" in result.message
    # This is a post-parse validation-pass failure (architecture.md Section
    # 5.1 rule 7) — it never carries a specific offending line, matching
    # the existing Cisco precedent.
    assert result.line_number is None


def test_arista_adapter__invalid_bgp_neighbor_ip__returns_parse_error() -> None:
    text = "hostname leaf-02\nrouter bgp 65002\n   neighbor 999.0.1.2 remote-as 65001\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_NEIGHBOR_IP
    assert "neighbor" in result.message.lower()
    assert result.line_number == 3
    assert result.line is not None and "999.0.1.2" in result.line


def test_arista_adapter__non_integer_bgp_remote_as__returns_parse_error() -> None:
    text = "hostname leaf-02\nrouter bgp 65002\n   neighbor 10.0.1.2 remote-as abc\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_REMOTE_AS
    assert "remote-as" in result.message.lower()
    assert result.line_number == 3


def test_arista_adapter__zero_bgp_remote_as__returns_parse_error() -> None:
    text = "hostname leaf-02\nrouter bgp 65002\n   neighbor 10.0.1.2 remote-as 0\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_REMOTE_AS
    assert result.line_number == 3


def test_arista_adapter__negative_bgp_remote_as__returns_parse_error() -> None:
    text = "hostname leaf-02\nrouter bgp 65002\n   neighbor 10.0.1.2 remote-as -5\n"

    result = AristaAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_REMOTE_AS
    assert result.line_number == 3
