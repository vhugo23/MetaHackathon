from pathlib import Path

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.domain.config import (
    AclAction,
    AdminState,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
)
from meta_rne.domain.errors import ParseError, ParseErrorCode

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "configs" / "cisco"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def test_cisco_adapter__empty_input__returns_parse_error() -> None:
    result = CiscoAdapter().parse("")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.EMPTY_CONFIGURATION


def test_cisco_adapter__whitespace_only_input__returns_parse_error() -> None:
    result = CiscoAdapter().parse("   \n\t\n  ")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.EMPTY_CONFIGURATION


def test_cisco_adapter__missing_hostname__returns_parse_error() -> None:
    text = "interface GigabitEthernet0/1\n ip address 10.0.0.1 255.255.255.252\n"

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MISSING_HOSTNAME


def test_cisco_adapter__malformed_hostname_declaration__returns_parse_error() -> None:
    result = CiscoAdapter().parse("hostname\n")

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MALFORMED_HOSTNAME
    assert result.line_number == 1


def test_cisco_adapter__malformed_interface_declaration__returns_parse_error() -> None:
    text = "hostname spine-01\ninterface\n"

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.MALFORMED_INTERFACE
    assert result.line_number == 2


def test_cisco_adapter__unknown_top_level_command__resets_previous_context() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/1\n"
        " ip address 10.0.0.1 255.255.255.0\n"
        "!\n"
        "some-unrecognized-command foo bar\n"
        " ip address 10.0.0.99 255.255.255.0\n"
        "!\n"
        "interface GigabitEthernet0/2\n"
        " ip address 10.0.0.2 255.255.255.0\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert [iface.name for iface in result.interfaces] == [
        "GigabitEthernet0/1",
        "GigabitEthernet0/2",
    ]
    gi1 = result.interfaces[0]
    # If the unknown top-level command had NOT reset the context, the
    # indented "ip address 10.0.0.99 ..." line right after it would have
    # been (incorrectly) applied to the still-open GigabitEthernet0/1
    # block instead of being ignored.
    assert gi1.ip_address == "10.0.0.1/24"


def test_cisco_adapter__interface_description__normalizes_description() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    gi1 = next(i for i in result.interfaces if i.name == "GigabitEthernet0/1")
    assert gi1.description == "Uplink to core-01"


def test_cisco_adapter__invalid_interface_ip_address__returns_parse_error() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/1\n"
        " ip address 999.0.0.1 255.255.255.0\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_INTERFACE_IP


def test_cisco_adapter__invalid_interface_subnet_mask__returns_parse_error() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/1\n"
        " ip address 10.0.0.1 255.0.255.0\n"  # non-contiguous mask
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_SUBNET_MASK


def test_cisco_adapter__interface_ip_address__normalizes_ip_and_mask() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    gi1 = next(i for i in result.interfaces if i.name == "GigabitEthernet0/1")
    assert gi1.ip_address == "10.0.0.1/30"


def test_cisco_adapter__interface_shutdown__normalizes_admin_state_down() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    gi2 = next(i for i in result.interfaces if i.name == "GigabitEthernet0/2")
    assert gi2.admin_state == AdminState.DOWN


def test_cisco_adapter__interface_no_shutdown_or_absent__normalizes_admin_state_up() -> None:
    absent_result = CiscoAdapter().parse("hostname spine-01\ninterface GigabitEthernet0/1\n")
    assert isinstance(absent_result, NormalizedConfiguration)
    assert absent_result.interfaces[0].admin_state == AdminState.UP

    explicit_result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))
    assert isinstance(explicit_result, NormalizedConfiguration)
    gi1 = next(i for i in explicit_result.interfaces if i.name == "GigabitEthernet0/1")
    assert gi1.admin_state == AdminState.UP


def test_cisco_adapter__acl_declaration__normalizes_acl_entries() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

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


def test_cisco_adapter__invalid_acl_direction__returns_parse_error() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/1\n"
        " ip access-group ACL-EXTERNAL-IN sideways\n"
        "!\n"
        "ip access-list extended ACL-EXTERNAL-IN\n"
        " permit ip any any\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_ACL_DIRECTION


def test_cisco_adapter__inbound_access_group__normalizes_acl_in() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    gi1 = next(i for i in result.interfaces if i.name == "GigabitEthernet0/1")
    assert gi1.acl_in == "ACL-EXTERNAL-IN"


def test_cisco_adapter__outbound_access_group__normalizes_acl_out() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    gi1 = next(i for i in result.interfaces if i.name == "GigabitEthernet0/1")
    assert gi1.acl_out == "ACL-EXTERNAL-OUT"


def test_cisco_adapter__acl_assignment_references_undeclared_acl__returns_parse_error() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/1\n"
        " ip access-group ACL-DOES-NOT-EXIST in\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.UNDECLARED_ACL_REFERENCE


def test_cisco_adapter__mixed_explicit_and_implicit_acl_sequences__remain_unique() -> None:
    text = (
        "hostname spine-01\n"
        "ip access-list extended ACL-MIXED\n"
        " 10 permit ip any any\n"
        " deny ip any any\n"
        " permit ip any any\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    acl = next(a for a in result.acls if a.name == "ACL-MIXED")
    assert [e.sequence for e in acl.entries] == [10, 20, 30]


def test_cisco_adapter__two_unnumbered_acl_entries__receive_unique_increasing_sequences() -> None:
    text = (
        "hostname spine-01\n"
        "ip access-list extended ACL-UNNUMBERED\n"
        " permit ip any any\n"
        " deny ip any any\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    acl = next(a for a in result.acls if a.name == "ACL-UNNUMBERED")
    assert [e.sequence for e in acl.entries] == [10, 20]


def test_cisco_adapter__invalid_bgp_neighbor_ip__returns_parse_error() -> None:
    text = "hostname spine-01\nrouter bgp 65001\n neighbor 999.0.0.1 remote-as 65002\n"

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_NEIGHBOR_IP
    assert result.line_number == 3


def test_cisco_adapter__non_integer_bgp_remote_as__returns_parse_error() -> None:
    text = "hostname spine-01\nrouter bgp 65001\n neighbor 10.0.0.2 remote-as abc\n"

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_REMOTE_AS
    assert result.line_number == 3


def test_cisco_adapter__non_positive_bgp_remote_as__returns_parse_error() -> None:
    text = "hostname spine-01\nrouter bgp 65001\n neighbor 10.0.0.2 remote-as 0\n"

    result = CiscoAdapter().parse(text)

    assert isinstance(result, ParseError)
    assert result.code == ParseErrorCode.INVALID_BGP_REMOTE_AS
    assert result.line_number == 3


def test_cisco_adapter__bgp_neighbor__normalizes_remote_as() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    assert result.routing.bgp_neighbors == (
        NormalizedBgpNeighbor(neighbor_ip="10.0.0.2", remote_as=65002),
    )


def test_cisco_adapter__normalized_collections__use_canonical_order() -> None:
    text = (
        "hostname spine-01\n"
        "interface GigabitEthernet0/2\n"
        "!\n"
        "interface GigabitEthernet0/1\n"
        "!\n"
        "ip access-list extended ACL-B\n"
        " permit ip any any\n"
        "!\n"
        "ip access-list extended ACL-A\n"
        " 20 permit ip any any\n"
        " 10 deny ip any any\n"
        "!\n"
        "router bgp 65001\n"
        " neighbor 10.0.0.10 remote-as 65010\n"
        " neighbor 10.0.0.2 remote-as 65002\n"
    )

    result = CiscoAdapter().parse(text)

    assert isinstance(result, NormalizedConfiguration)
    assert [i.name for i in result.interfaces] == ["GigabitEthernet0/1", "GigabitEthernet0/2"]
    assert [a.name for a in result.acls] == ["ACL-A", "ACL-B"]
    # ACL-A's entries were declared 20-then-10: canonical order is by
    # sequence (10, 20), never alphabetically by protocol/source/dest.
    assert [e.sequence for e in result.acls[0].entries] == [10, 20]
    # Numeric IPv4 order, not lexicographic: "10.0.0.2" sorts before
    # "10.0.0.10" numerically but AFTER it lexicographically.
    assert [n.neighbor_ip for n in result.routing.bgp_neighbors] == [
        "10.0.0.2",
        "10.0.0.10",
    ]


def test_cisco_adapter__valid_config__returns_normalized_config() -> None:
    result = CiscoAdapter().parse(_load_fixture("cisco_required_acl_assigned.txt"))

    assert isinstance(result, NormalizedConfiguration)
    assert result.hostname == "spine-01"
    assert len(result.interfaces) == 2
    assert len(result.routing.bgp_neighbors) == 1
    assert len(result.acls) == 2


def test_cisco_adapter__same_input_parsed_twice__returns_equal_normalized_config() -> None:
    text = _load_fixture("cisco_required_acl_assigned.txt")
    adapter = CiscoAdapter()

    assert adapter.parse(text) == adapter.parse(text)
