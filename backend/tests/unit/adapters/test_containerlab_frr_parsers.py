import json
from collections.abc import Callable

import pytest

from meta_rne.adapters.containerlab_frr.parsers import (
    OutputParseError,
    parse_bgp_summary,
    parse_frr_routes,
    parse_ip_json_interfaces,
    parse_ip_oneline_interfaces,
    parse_ping_summary,
)
from meta_rne.domain.operational_state import (
    BgpSessionState,
    InterfaceOperState,
    NextHop,
)

Fixture = Callable[[str, str, str], str]


def _by_name(items: tuple, attribute: str = "name") -> dict:  # type: ignore[type-arg]
    return {getattr(item, attribute): item for item in items}


class TestRouterInterfaces:
    def test_healthy_leaf_interfaces_and_addresses(self, read_fixture: Fixture) -> None:
        interfaces = _by_name(
            parse_ip_json_interfaces(read_fixture("baseline", "leaf-1", "ip_j_addr.json"))
        )
        assert interfaces["eth1"].oper_state is InterfaceOperState.UP
        # Up interfaces also carry an IPv6 link-local address; none is dropped.
        assert interfaces["eth1"].addresses[0] == "10.255.0.1/31"
        assert interfaces["eth2"].addresses[0] == "10.255.0.3/31"
        assert interfaces["eth3"].addresses[0] == "10.1.1.1/24"
        assert all(a.startswith("fe80::") for a in interfaces["eth1"].addresses[1:])
        assert "10.0.0.11/32" in interfaces["lo"].addresses

    def test_multiple_addresses_on_one_interface(self, read_fixture: Fixture) -> None:
        interfaces = _by_name(
            parse_ip_json_interfaces(read_fixture("baseline", "leaf-1", "ip_j_addr.json"))
        )
        # eth0: management IPv4, ULA IPv6 and a link-local address.
        assert len(interfaces["eth0"].addresses) == 3
        assert "172.31.250.4/24" in interfaces["eth0"].addresses
        assert "3fff:172:31:250::4/64" in interfaces["eth0"].addresses
        assert any(a.startswith("fe80::") for a in interfaces["eth0"].addresses)

    def test_loopback_unknown_operstate_is_unknown_not_down(self, read_fixture: Fixture) -> None:
        interfaces = _by_name(
            parse_ip_json_interfaces(read_fixture("baseline", "leaf-1", "ip_j_addr.json"))
        )
        assert interfaces["lo"].oper_state is InterfaceOperState.UNKNOWN

    def test_down_interface(self, read_fixture: Fixture) -> None:
        interfaces = _by_name(
            parse_ip_json_interfaces(read_fixture("degraded-active", "leaf-1", "ip_j_addr.json"))
        )
        assert interfaces["eth1"].oper_state is InterfaceOperState.DOWN
        # The administratively-down interface keeps its configured address.
        assert interfaces["eth1"].addresses == ("10.255.0.1/31",)
        assert interfaces["eth2"].oper_state is InterfaceOperState.UP

    def test_missing_operstate_is_unknown(self) -> None:
        text = json.dumps([{"ifname": "eth9", "addr_info": []}])
        (interface,) = parse_ip_json_interfaces(text)
        assert interface.oper_state is InterfaceOperState.UNKNOWN

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not json",
            "{}",
            json.dumps(["x"]),
            json.dumps([{"operstate": "UP"}]),
            json.dumps([{"ifname": "eth0", "addr_info": "no"}]),
            json.dumps([{"ifname": "eth0", "addr_info": [{"local": "10.0.0.1"}]}]),
            json.dumps([{"ifname": "eth0", "addr_info": [{"local": "bad", "prefixlen": 8}]}]),
        ],
    )
    def test_malformed_output_raises(self, text: str) -> None:
        with pytest.raises(OutputParseError):
            parse_ip_json_interfaces(text)


class TestHostInterfaces:
    def test_host_interfaces_and_addresses(self, read_fixture: Fixture) -> None:
        interfaces = _by_name(
            parse_ip_oneline_interfaces(
                read_fixture("baseline", "host-1", "ip_o_link.txt"),
                read_fixture("baseline", "host-1", "ip_o_addr.txt"),
            )
        )
        assert set(interfaces) == {"lo", "eth0", "eth1"}
        assert interfaces["eth1"].oper_state is InterfaceOperState.UP
        assert interfaces["eth1"].addresses[0] == "10.1.1.10/24"
        assert interfaces["lo"].oper_state is InterfaceOperState.UNKNOWN
        assert len(interfaces["eth0"].addresses) == 3

    def test_down_host_interface(self) -> None:
        link = (
            "5: eth1@if4: <BROADCAST,MULTICAST> mtu 9500 qdisc noqueue state DOWN "
            "\\    link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff\n"
        )
        (interface,) = parse_ip_oneline_interfaces(link, "")
        assert interface.name == "eth1"
        assert interface.oper_state is InterfaceOperState.DOWN
        assert interface.addresses == ()

    def test_link_without_state_is_unknown(self) -> None:
        (interface,) = parse_ip_oneline_interfaces("5: eth1: <BROADCAST> mtu 1500\n", "")
        assert interface.oper_state is InterfaceOperState.UNKNOWN

    @pytest.mark.parametrize(
        ("link", "addr"),
        [
            ("", ""),
            ("garbage\n", ""),
            ("5: eth1: <UP> state UP\n", "garbage\n"),
            ("5: eth1: <UP> state UP\n", "9: eth9    inet 10.0.0.1/24 scope global eth9\n"),
        ],
    )
    def test_malformed_output_raises(self, link: str, addr: str) -> None:
        with pytest.raises(OutputParseError):
            parse_ip_oneline_interfaces(link, addr)


class TestBgpSummary:
    def test_established_neighbors(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(read_fixture("baseline", "leaf-1", "show_bgp_summary.json"))
        assert bgp.local_as == 65101
        assert bgp.router_id == "10.0.0.11"
        neighbors = _by_name(bgp.neighbors, "neighbor_ip")
        assert set(neighbors) == {"10.255.0.0", "10.255.0.2"}
        assert bgp.established_count == 2
        for neighbor in neighbors.values():
            assert neighbor.state is BgpSessionState.ESTABLISHED
            assert neighbor.raw_state == "Established"
            assert neighbor.remote_as == 65000

    def test_prefix_counts_and_uptime_for_established(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(read_fixture("baseline", "leaf-1", "show_bgp_summary.json"))
        for neighbor in bgp.neighbors:
            assert neighbor.prefixes_received == 3
            assert neighbor.uptime_ms is not None
            assert neighbor.uptime_ms > 0

    def test_spine_remote_as_per_leaf(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(read_fixture("baseline", "spine-1", "show_bgp_summary.json"))
        assert bgp.local_as == 65000
        remote = {n.neighbor_ip: n.remote_as for n in bgp.neighbors}
        assert remote == {"10.255.0.1": 65101, "10.255.0.5": 65102}

    def test_active_neighbor(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(read_fixture("degraded-active", "leaf-1", "show_bgp_summary.json"))
        neighbors = _by_name(bgp.neighbors, "neighbor_ip")
        failed = neighbors["10.255.0.0"]
        assert failed.state is BgpSessionState.ACTIVE
        assert failed.raw_state == "Active"
        assert failed.remote_as == 65000
        # FRR reports pfxRcd=0 and a time-in-state for a non-Established
        # session; neither is an observation of a learned prefix/uptime.
        assert failed.prefixes_received is None
        assert failed.uptime_ms is None
        assert neighbors["10.255.0.2"].is_established
        assert bgp.established_count == 1

    def test_idle_neighbor(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(read_fixture("degraded-idle", "leaf-1", "show_bgp_summary.json"))
        failed = _by_name(bgp.neighbors, "neighbor_ip")["10.255.0.0"]
        assert failed.state is BgpSessionState.IDLE
        assert failed.prefixes_received is None

    def test_unrecognized_real_state_is_unknown_and_keeps_raw(self, read_fixture: Fixture) -> None:
        bgp = parse_bgp_summary(
            read_fixture("degraded-clearing", "leaf-1", "show_bgp_summary.json")
        )
        transient = _by_name(bgp.neighbors, "neighbor_ip")["10.255.0.0"]
        assert transient.state is BgpSessionState.UNKNOWN
        assert transient.raw_state == "Clearing"
        assert transient.prefixes_received is None

    def _summary(self, peer: object) -> str:
        return json.dumps(
            {"ipv4Unicast": {"as": 65101, "routerId": "10.0.0.11", "peers": {"10.255.0.0": peer}}}
        )

    def test_peer_without_state_is_unknown(self) -> None:
        (neighbor,) = parse_bgp_summary(self._summary({"remoteAs": 65000})).neighbors
        assert neighbor.state is BgpSessionState.UNKNOWN
        assert neighbor.raw_state is None
        assert neighbor.remote_as == 65000

    def test_peer_with_non_string_state_is_unknown(self) -> None:
        (neighbor,) = parse_bgp_summary(self._summary({"state": 7})).neighbors
        assert neighbor.state is BgpSessionState.UNKNOWN
        assert neighbor.raw_state is None

    def test_non_object_peer_is_unknown(self) -> None:
        (neighbor,) = parse_bgp_summary(self._summary("oops")).neighbors
        assert neighbor.state is BgpSessionState.UNKNOWN
        assert neighbor.remote_as is None

    def test_garbled_established_numbers_become_none_not_defaults(self) -> None:
        peer = {"state": "Established", "remoteAs": "65000", "pfxRcd": -1, "peerUptimeMsec": True}
        (neighbor,) = parse_bgp_summary(self._summary(peer)).neighbors
        assert neighbor.state is BgpSessionState.ESTABLISHED
        assert (neighbor.remote_as, neighbor.prefixes_received, neighbor.uptime_ms) == (
            None,
            None,
            None,
        )

    def test_no_peers_is_valid(self) -> None:
        text = json.dumps({"ipv4Unicast": {"as": 65101, "routerId": "10.0.0.11", "peers": {}}})
        assert parse_bgp_summary(text).neighbors == ()

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{not json",
            "[]",
            "{}",
            json.dumps({"ipv4Unicast": []}),
            json.dumps({"ipv4Unicast": {"routerId": "10.0.0.1", "peers": {}}}),
            json.dumps({"ipv4Unicast": {"as": 1, "peers": {}}}),
            json.dumps({"ipv4Unicast": {"as": 1, "routerId": "10.0.0.1"}}),
            json.dumps({"ipv4Unicast": {"as": 1, "routerId": "bad", "peers": {}}}),
            json.dumps({"ipv4Unicast": {"as": 1, "routerId": "10.0.0.1", "peers": {"x": {}}}}),
        ],
    )
    def test_malformed_output_raises(self, text: str) -> None:
        with pytest.raises(OutputParseError):
            parse_bgp_summary(text)


class TestRoutes:
    def test_ecmp_route_with_next_hops_and_interfaces(self, read_fixture: Fixture) -> None:
        routes = _by_name(
            parse_frr_routes(read_fixture("baseline", "leaf-1", "show_ip_route.json")),
            "prefix",
        )
        remote_lan = routes["10.1.2.0/24"]
        assert remote_lan.protocol == "bgp"
        assert remote_lan.ecmp_path_count == 2
        assert remote_lan.is_ecmp
        assert set(remote_lan.next_hops) == {
            NextHop("10.255.0.0", "eth1"),
            NextHop("10.255.0.2", "eth2"),
        }

    def test_single_path_route(self, read_fixture: Fixture) -> None:
        routes = _by_name(
            parse_frr_routes(read_fixture("baseline", "leaf-1", "show_ip_route.json")),
            "prefix",
        )
        spine_loopback = routes["10.0.0.1/32"]
        assert spine_loopback.next_hops == (NextHop("10.255.0.0", "eth1"),)
        assert not spine_loopback.is_ecmp

    def test_connected_route_has_interface_but_no_gateway(self, read_fixture: Fixture) -> None:
        routes = _by_name(
            parse_frr_routes(read_fixture("baseline", "leaf-1", "show_ip_route.json")),
            "prefix",
        )
        assert routes["10.1.1.0/24"].protocol == "connected"
        assert routes["10.1.1.0/24"].next_hops == (NextHop(None, "eth3"),)

    def test_one_prefix_can_have_several_protocols(self, read_fixture: Fixture) -> None:
        routes = parse_frr_routes(read_fixture("baseline", "leaf-1", "show_ip_route.json"))
        protocols = {r.protocol for r in routes if r.prefix == "10.0.0.11/32"}
        assert protocols == {"local", "connected"}

    def test_degraded_leaf_drops_to_single_path(self, read_fixture: Fixture) -> None:
        routes = _by_name(
            parse_frr_routes(read_fixture("degraded-active", "leaf-1", "show_ip_route.json")),
            "prefix",
        )
        remote_lan = routes["10.1.2.0/24"]
        assert remote_lan.next_hops == (NextHop("10.255.0.2", "eth2"),)
        assert remote_lan.ecmp_path_count == 1
        # The failed spine's loopback is no longer reachable at all.
        assert "10.0.0.1/32" not in routes

    def test_other_leaf_also_degrades(self, read_fixture: Fixture) -> None:
        routes = _by_name(
            parse_frr_routes(read_fixture("degraded-active", "leaf-2", "show_ip_route.json")),
            "prefix",
        )
        assert routes["10.1.1.0/24"].next_hops == (NextHop("10.255.0.6", "eth2"),)

    def test_next_hop_without_active_flag_is_not_counted(self, read_fixture: Fixture) -> None:
        # The degraded-active capture contains a RIB next-hop (10.255.0.0 via
        # eth1) that has fib=true but no "active" key.
        raw = json.loads(read_fixture("degraded-active", "leaf-1", "show_ip_route.json"))
        listed = {nh["ip"]: nh.get("active") for nh in raw["10.1.2.0/24"][0]["nexthops"]}
        assert listed == {"10.255.0.0": None, "10.255.0.2": True}

    def test_uninstalled_and_hopless_entries_are_omitted(self) -> None:
        text = json.dumps(
            {
                "10.9.0.0/24": [
                    {
                        "protocol": "bgp",
                        "installed": False,
                        "nexthops": [{"ip": "10.0.0.1", "active": True}],
                    }
                ],
                "10.9.1.0/24": [
                    {"protocol": "bgp", "installed": True, "nexthops": [{"ip": "10.0.0.1"}]}
                ],
            }
        )
        assert parse_frr_routes(text) == ()

    def test_next_hops_are_sorted_deterministically(self) -> None:
        text = json.dumps(
            {
                "10.9.0.0/24": [
                    {
                        "protocol": "bgp",
                        "installed": True,
                        "nexthops": [
                            {"ip": "10.255.0.2", "interfaceName": "eth2", "active": True},
                            {"ip": "10.255.0.0", "interfaceName": "eth1", "active": True},
                        ],
                    }
                ]
            }
        )
        (route,) = parse_frr_routes(text)
        assert route.next_hops == (NextHop("10.255.0.0", "eth1"), NextHop("10.255.0.2", "eth2"))

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{oops",
            "[]",
            json.dumps({"10.0.0.0/8": "x"}),
            json.dumps({"10.0.0.0/8": ["x"]}),
            json.dumps({"10.0.0.0/8": [{"installed": True, "nexthops": []}]}),
            json.dumps({"10.0.0.0/8": [{"protocol": "bgp", "installed": True}]}),
            json.dumps({"10.0.0.0/8": [{"protocol": "bgp", "installed": True, "nexthops": ["x"]}]}),
            json.dumps(
                {
                    "bad": [
                        {
                            "protocol": "bgp",
                            "installed": True,
                            "nexthops": [{"ip": "10.0.0.1", "active": True}],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "10.0.0.0/8": [
                        {"protocol": "bgp", "installed": True, "nexthops": [{"active": True}]}
                    ]
                }
            ),
        ],
    )
    def test_malformed_output_raises(self, text: str) -> None:
        with pytest.raises(OutputParseError):
            parse_frr_routes(text)


class TestPing:
    def test_full_success(self, read_fixture: Fixture) -> None:
        text = read_fixture("baseline", "host-1", "ping_10.1.2.10.txt")
        assert parse_ping_summary(text) == (3, 3)

    def test_total_loss_is_an_observation(self) -> None:
        text = (
            "--- 10.1.2.10 ping statistics ---\n"
            "3 packets transmitted, 0 packets received, 100% packet loss\n"
        )
        assert parse_ping_summary(text) == (3, 0)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "ping: bad address",
            "0 packets transmitted, 0 packets received",
            "3 packets transmitted, 4 packets received",
        ],
    )
    def test_no_valid_summary_raises(self, text: str) -> None:
        with pytest.raises(OutputParseError):
            parse_ping_summary(text)
