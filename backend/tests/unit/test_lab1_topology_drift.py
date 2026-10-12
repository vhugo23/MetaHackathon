"""Drift guard (NPE-1E1A): ``meta_rne.lab1_topology`` must equal the
repository's own Lab 1 definition. Test-time, dependency-free parsing of the
(very regular) ``lab/topology.clab.yml`` and ``lab/frr/<router>/frr.conf``;
production code never touches the filesystem or a YAML parser."""

import re
from pathlib import Path

from meta_rne.lab1_identity import LAB1_NODE_DEVICE_IDS
from meta_rne.lab1_topology import (
    LAB1_ECMP_EXPECTATIONS,
    LAB1_HOST_NODE_IDS,
    LAB1_LEAF_NODE_IDS,
    LAB1_LINKS,
    LAB1_NODE_IDS,
    LAB1_ROUTER_LINKS,
    LAB1_ROUTER_NODE_IDS,
    LAB1_SPINE_NODE_IDS,
    EcmpExpectation,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROUTERS = ("spine-1", "spine-2", "leaf-1", "leaf-2")


def _read(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


_TOPOLOGY = _read("lab/topology.clab.yml")


def _nodes_section() -> str:
    return _TOPOLOGY.split("\n  nodes:\n", 1)[1].split("\n  links:\n", 1)[0]


def _yaml_node_names() -> list[str]:
    return re.findall(r"^ {4}([\w-]+):\s*$", _nodes_section(), re.MULTILINE)


def _yaml_links() -> list[tuple[str, str]]:
    section = _TOPOLOGY.split("\n  links:\n", 1)[1]
    return re.findall(r'endpoints:\s*\["([^"]+)",\s*"([^"]+)"\]', section)


def _yaml_host_addresses() -> dict[str, str]:
    """``host:iface`` -> address from each host's ``ip addr add`` exec line."""
    result: dict[str, str] = {}
    blocks = re.split(r"^ {4}([\w-]+):\s*$", _nodes_section(), flags=re.MULTILINE)
    for name, body in zip(blocks[1::2], blocks[2::2], strict=True):
        for address, interface in re.findall(
            r"ip addr add (\d+\.\d+\.\d+\.\d+)/\d+ dev (\S+)", body
        ):
            result[f"{name}:{interface}"] = address
    return result


def _frr_interface_addresses(router: str) -> dict[str, str]:
    result: dict[str, str] = {}
    interface: str | None = None
    for line in _read(f"lab/frr/{router}/frr.conf").splitlines():
        match = re.match(r"^interface (\S+)$", line)
        if match:
            interface = match.group(1)
            continue
        address = re.match(r"^ ip address (\d+\.\d+\.\d+\.\d+)/\d+$", line)
        if address and interface is not None:
            result[f"{router}:{interface}"] = address.group(1)
        if line.startswith("router "):
            interface = None
    return result


def _declared_addresses() -> dict[str, str]:
    result = _yaml_host_addresses()
    for router in _ROUTERS:
        result.update(_frr_interface_addresses(router))
    return result


def _frr_neighbors(router: str) -> set[str]:
    text = _read(f"lab/frr/{router}/frr.conf")
    return set(re.findall(r"^ neighbor (\S+) remote-as \d+$", text, re.MULTILINE))


def _frr_networks(router: str) -> set[str]:
    return set(
        re.findall(r"^ {2}network (\S+)$", _read(f"lab/frr/{router}/frr.conf"), re.MULTILINE)
    )


def _frr_maximum_paths(router: str) -> int | None:
    text = _read(f"lab/frr/{router}/frr.conf")
    match = re.search(r"^ {2}maximum-paths (\d+)$", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def _to_int(address: str) -> int:
    return int.from_bytes(bytes(int(part) for part in address.split(".")), "big")


class TestNodes:
    def test_six_declared_nodes_match_the_topology_file(self) -> None:
        assert sorted(_yaml_node_names()) == sorted(LAB1_NODE_IDS)
        assert len(LAB1_NODE_IDS) == 6

    def test_router_and_host_split(self) -> None:
        assert set(LAB1_ROUTER_NODE_IDS) == set(LAB1_NODE_DEVICE_IDS)
        assert set(LAB1_HOST_NODE_IDS) == {"host-1", "host-2"}
        assert {n for n in _yaml_node_names() if n.startswith(("spine", "leaf"))} == set(
            LAB1_NODE_DEVICE_IDS
        )

    def test_device_id_mapping(self) -> None:
        assert LAB1_NODE_DEVICE_IDS == {node: f"lab1-{node}" for node in _ROUTERS}
        for router in _ROUTERS:
            assert f"hostname {router}" in _read(f"lab/frr/{router}/frr.conf")


class TestLinks:
    def test_six_links_with_exact_endpoint_pairs(self) -> None:
        declared = {
            frozenset(
                {
                    f"{link.endpoint_a.node_id}:{link.endpoint_a.interface_name}",
                    f"{link.endpoint_b.node_id}:{link.endpoint_b.interface_name}",
                }
            )
            for link in LAB1_LINKS
        }
        assert len(LAB1_LINKS) == 6 and len(declared) == 6
        assert declared == {frozenset(pair) for pair in _yaml_links()}

    def test_four_fabric_and_two_access_links(self) -> None:
        assert len(LAB1_ROUTER_LINKS) == 4
        access = [link for link in LAB1_LINKS if not link.is_router_link]
        assert {(link.endpoint_a.node_id, link.endpoint_b.node_id) for link in access} == {
            ("host-1", "leaf-1"),
            ("host-2", "leaf-2"),
        }

    def test_endpoint_addresses_match_the_lab_configuration(self) -> None:
        addresses = _declared_addresses()
        for link in LAB1_LINKS:
            for endpoint in (link.endpoint_a, link.endpoint_b):
                key = f"{endpoint.node_id}:{endpoint.interface_name}"
                assert addresses[key] == endpoint.address, key

    def test_router_link_addresses_form_a_slash31(self) -> None:
        for link in LAB1_ROUTER_LINKS:
            a, b = _to_int(link.endpoint_a.address), _to_int(link.endpoint_b.address)
            assert a // 2 == b // 2 and a != b

    def test_each_routers_bgp_neighbors_are_its_declared_peers(self) -> None:
        for router in _ROUTERS:
            peers = set()
            for link in LAB1_ROUTER_LINKS:
                if link.endpoint_a.node_id == router:
                    peers.add(link.endpoint_b.address)
                elif link.endpoint_b.node_id == router:
                    peers.add(link.endpoint_a.address)
            assert _frr_neighbors(router) == peers, router


class TestTiers:
    def test_spine_and_leaf_ids_match_the_topology_file(self) -> None:
        names = _yaml_node_names()
        assert set(LAB1_SPINE_NODE_IDS) == {n for n in names if n.startswith("spine")}
        assert set(LAB1_LEAF_NODE_IDS) == {n for n in names if n.startswith("leaf")}
        for link in LAB1_ROUTER_LINKS:  # every fabric link is leaf-spine
            assert {link.endpoint_a.node_id[:4], link.endpoint_b.node_id[:4]} == {"leaf", "spin"}


class TestEcmpExpectations:
    def test_expectations_derive_from_the_lab_configuration(self) -> None:
        leaves = [r for r in _ROUTERS if r.startswith("leaf")]
        derived = set()
        for leaf in leaves:
            (other,) = [r for r in leaves if r != leaf]
            (remote_prefix,) = [p for p in _frr_networks(other) if p.endswith("/24")]
            fabric_links = [
                link
                for link in LAB1_ROUTER_LINKS
                if leaf in (link.endpoint_a.node_id, link.endpoint_b.node_id)
            ]
            maximum_paths = _frr_maximum_paths(leaf)
            assert maximum_paths == len(fabric_links) == 2
            derived.add(EcmpExpectation(leaf, remote_prefix, other, maximum_paths))
        assert set(LAB1_ECMP_EXPECTATIONS) == derived
        assert len(LAB1_ECMP_EXPECTATIONS) == 2
