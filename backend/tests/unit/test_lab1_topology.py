import pytest

from meta_rne.lab1_topology import (
    LAB1_ECMP_EXPECTATIONS,
    LAB1_LINKS,
    LAB1_ROUTER_LINKS,
    EcmpExpectation,
    PhysicalLink,
    TopologyEndpoint,
    ecmp_next_hop_lost_with_link,
)


def test_canonical_link_ids_and_order() -> None:
    assert [link.link_id for link in LAB1_LINKS] == sorted(link.link_id for link in LAB1_LINKS)
    assert [link.link_id for link in LAB1_ROUTER_LINKS] == [
        "leaf-1:eth1--spine-1:eth1",
        "leaf-1:eth2--spine-2:eth1",
        "leaf-2:eth1--spine-1:eth2",
        "leaf-2:eth2--spine-2:eth2",
    ]


def test_non_canonical_endpoint_order_is_rejected() -> None:
    leaf = TopologyEndpoint("leaf-1", "eth1", "10.255.0.1")
    spine = TopologyEndpoint("spine-1", "eth1", "10.255.0.0")
    assert PhysicalLink(leaf, spine).link_id == "leaf-1:eth1--spine-1:eth1"
    with pytest.raises(ValueError, match="canonical"):
        PhysicalLink(spine, leaf)


def test_same_node_link_and_bad_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        PhysicalLink(
            TopologyEndpoint("leaf-1", "eth1", "10.255.0.1"),
            TopologyEndpoint("leaf-1", "eth2", "10.255.0.3"),
        )
    with pytest.raises(ValueError):
        TopologyEndpoint("leaf-1", "eth1", "not-an-ip")
    with pytest.raises(ValueError):
        EcmpExpectation("leaf-1", "10.1.2.0/24", "leaf-2", 1)


def _link(link_id: str) -> PhysicalLink:
    return next(link for link in LAB1_LINKS if link.link_id == link_id)


def test_each_fabric_link_removes_exactly_the_path_via_its_spine() -> None:
    leaf_1, leaf_2 = LAB1_ECMP_EXPECTATIONS
    assert (leaf_1.node_id, leaf_1.origin_node_id) == ("leaf-1", "leaf-2")
    expected = {
        "leaf-1:eth1--spine-1:eth1": ("10.255.0.0", "10.255.0.4"),
        "leaf-1:eth2--spine-2:eth1": ("10.255.0.2", "10.255.0.6"),
        "leaf-2:eth1--spine-1:eth2": ("10.255.0.0", "10.255.0.4"),
        "leaf-2:eth2--spine-2:eth2": ("10.255.0.2", "10.255.0.6"),
    }
    for link_id, (leaf_1_next_hop, leaf_2_next_hop) in expected.items():
        assert ecmp_next_hop_lost_with_link(leaf_1, _link(link_id)) == leaf_1_next_hop
        assert ecmp_next_hop_lost_with_link(leaf_2, _link(link_id)) == leaf_2_next_hop


def test_host_access_links_and_unrelated_leaves_remove_no_ecmp_path() -> None:
    for expectation in LAB1_ECMP_EXPECTATIONS:
        assert ecmp_next_hop_lost_with_link(expectation, _link("host-1:eth1--leaf-1:eth3")) is None
        assert ecmp_next_hop_lost_with_link(expectation, _link("host-2:eth1--leaf-2:eth3")) is None
    # An expectation between two other nodes is untouched by a leaf-1 link.
    unrelated = EcmpExpectation("leaf-2", "10.1.1.0/24", "spine-1", 2)
    assert ecmp_next_hop_lost_with_link(unrelated, _link("leaf-1:eth2--spine-2:eth1")) is None
