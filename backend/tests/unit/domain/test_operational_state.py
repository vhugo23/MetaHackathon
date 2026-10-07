from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.operational_state import (
    BgpNeighborObservation,
    BgpObservation,
    BgpSessionState,
    CollectionSource,
    FabricOperationalState,
    InterfaceObservation,
    InterfaceOperState,
    NextHop,
    NodeRole,
    NormalizedOperationalState,
    ObservationFacet,
    ReachabilityObservation,
    RouteObservation,
    UnavailableObservation,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
SOURCE = CollectionSource(collector="c", lab_name="lab", collection_id="run-1")


def _neighbor(
    state: BgpSessionState = BgpSessionState.ESTABLISHED,
    ip: str = "10.255.0.0",
    prefixes: int | None = 3,
    uptime: int | None = 1000,
) -> BgpNeighborObservation:
    return BgpNeighborObservation(
        neighbor_ip=ip,
        remote_as=65000,
        state=state,
        raw_state=state.value,
        prefixes_received=prefixes,
        uptime_ms=uptime,
    )


def _router(**overrides: object) -> NormalizedOperationalState:
    fields: dict[str, object] = {
        "node_id": "leaf-1",
        "role": NodeRole.ROUTER,
        "collected_at": NOW,
        "source": SOURCE,
        "interfaces": (),
        "bgp": BgpObservation(local_as=65101, router_id="10.0.0.11", neighbors=()),
        "routes": (),
        "reachability": (),
    }
    fields.update(overrides)
    return NormalizedOperationalState(**fields)  # type: ignore[arg-type]


class TestInterfaceObservation:
    def test_valid_with_multiple_addresses(self) -> None:
        interface = InterfaceObservation(
            "eth0", InterfaceOperState.UP, ("172.31.250.4/24", "3fff:172:31:250::4/64")
        )
        assert len(interface.addresses) == 2

    def test_rejects_malformed_address(self) -> None:
        with pytest.raises(ValueError):
            InterfaceObservation("eth0", InterfaceOperState.UP, ("not-an-ip",))

    def test_rejects_duplicate_addresses_and_empty_name(self) -> None:
        with pytest.raises(ValueError):
            InterfaceObservation("eth0", InterfaceOperState.UP, ("10.0.0.1/24", "10.0.0.1/24"))
        with pytest.raises(ValueError):
            InterfaceObservation(" ", InterfaceOperState.UP, ())

    def test_unknown_is_distinct_from_down(self) -> None:
        assert InterfaceOperState.UNKNOWN != InterfaceOperState.DOWN


class TestBgpNeighborObservation:
    def test_established_carries_prefixes_and_uptime(self) -> None:
        neighbor = _neighbor()
        assert neighbor.is_established
        assert neighbor.prefixes_received == 3
        assert neighbor.uptime_ms == 1000

    @pytest.mark.parametrize(
        "state",
        [
            BgpSessionState.IDLE,
            BgpSessionState.ACTIVE,
            BgpSessionState.CONNECT,
            BgpSessionState.UNKNOWN,
        ],
    )
    def test_non_established_must_not_carry_prefixes_or_uptime(
        self, state: BgpSessionState
    ) -> None:
        with pytest.raises(ValueError, match="only valid for an Established"):
            _neighbor(state=state)
        neighbor = _neighbor(state=state, prefixes=None, uptime=None)
        assert not neighbor.is_established

    def test_unknown_state_is_not_idle_or_active(self) -> None:
        assert BgpSessionState.UNKNOWN not in (BgpSessionState.IDLE, BgpSessionState.ACTIVE)

    def test_rejects_bad_ip_and_non_positive_as(self) -> None:
        with pytest.raises(ValueError):
            _neighbor(ip="nope")
        with pytest.raises(ValueError):
            BgpNeighborObservation("10.0.0.1", 0, BgpSessionState.IDLE, "Idle", None, None)


class TestBgpObservation:
    def test_established_count_ignores_unknown_and_down(self) -> None:
        bgp = BgpObservation(
            65101,
            "10.0.0.11",
            (
                _neighbor(),
                _neighbor(BgpSessionState.ACTIVE, "10.255.0.2", None, None),
                _neighbor(BgpSessionState.UNKNOWN, "10.255.0.4", None, None),
            ),
        )
        assert bgp.established_count == 1

    def test_rejects_duplicate_neighbors(self) -> None:
        with pytest.raises(ValueError):
            BgpObservation(65101, "10.0.0.11", (_neighbor(), _neighbor()))


class TestRouteObservation:
    def test_ecmp_path_count_is_derived(self) -> None:
        single = RouteObservation("10.1.2.0/24", "bgp", (NextHop("10.255.0.2", "eth2"),))
        ecmp = RouteObservation(
            "10.1.2.0/24", "bgp", (NextHop("10.255.0.0", "eth1"), NextHop("10.255.0.2", "eth2"))
        )
        assert (single.ecmp_path_count, single.is_ecmp) == (1, False)
        assert (ecmp.ecmp_path_count, ecmp.is_ecmp) == (2, True)

    def test_no_stored_ecmp_field(self) -> None:
        assert "ecmp_path_count" not in RouteObservation.__slots__

    def test_requires_next_hop_and_valid_prefix(self) -> None:
        with pytest.raises(ValueError):
            RouteObservation("10.1.2.0/24", "bgp", ())
        with pytest.raises(ValueError):
            RouteObservation("nope", "bgp", (NextHop(None, "eth1"),))

    def test_rejects_duplicate_next_hops(self) -> None:
        with pytest.raises(ValueError):
            RouteObservation(
                "10.0.0.0/8", "bgp", (NextHop("10.0.0.1", "e"), NextHop("10.0.0.1", "e"))
            )

    def test_next_hop_needs_ip_or_interface(self) -> None:
        with pytest.raises(ValueError):
            NextHop(None, None)
        assert NextHop(None, "lo").ip is None


class TestReachabilityObservation:
    def test_total_loss_is_a_valid_observation(self) -> None:
        loss = ReachabilityObservation("host-1", "10.1.2.10", sent=3, received=0)
        assert not loss.succeeded
        assert ReachabilityObservation("host-1", "10.1.2.10", 3, 3).succeeded

    def test_rejects_impossible_counts(self) -> None:
        with pytest.raises(ValueError):
            ReachabilityObservation("host-1", "10.1.2.10", 3, 4)
        with pytest.raises(ValueError):
            ReachabilityObservation("host-1", "10.1.2.10", 0, 0)


class TestNormalizedOperationalState:
    def test_rejects_naive_and_non_utc_timestamps(self) -> None:
        with pytest.raises(ValueError):
            _router(collected_at=datetime(2026, 10, 7))
        with pytest.raises(ValueError):
            _router(collected_at=NOW.astimezone(timezone(timedelta(hours=2))))

    def test_unavailable_facet_must_carry_no_data(self) -> None:
        interface = InterfaceObservation("eth0", InterfaceOperState.UP, ())
        with pytest.raises(ValueError, match="no data"):
            _router(
                interfaces=(interface,),
                unavailable=(UnavailableObservation(ObservationFacet.INTERFACES, "boom"),),
            )

    def test_router_needs_bgp_or_unavailable_bgp(self) -> None:
        with pytest.raises(ValueError):
            _router(bgp=None)
        state = _router(
            bgp=None, unavailable=(UnavailableObservation(ObservationFacet.BGP, "failed"),)
        )
        assert state.is_unavailable(ObservationFacet.BGP)
        assert not state.is_unavailable(ObservationFacet.ROUTES)

    def test_router_cannot_have_bgp_and_unavailable_bgp(self) -> None:
        with pytest.raises(ValueError):
            _router(unavailable=(UnavailableObservation(ObservationFacet.BGP, "failed"),))

    def test_host_has_no_bgp(self) -> None:
        host = _router(node_id="host-1", role=NodeRole.HOST, bgp=None)
        assert host.bgp is None
        with pytest.raises(ValueError, match="host has no BGP"):
            _router(node_id="host-1", role=NodeRole.HOST)

    def test_rejects_duplicate_interfaces_and_facets(self) -> None:
        interface = InterfaceObservation("eth0", InterfaceOperState.UP, ())
        with pytest.raises(ValueError):
            _router(interfaces=(interface, interface))
        problem = UnavailableObservation(ObservationFacet.ROUTES, "x")
        with pytest.raises(ValueError):
            _router(unavailable=(problem, problem))

    def test_unavailable_reason_required(self) -> None:
        with pytest.raises(ValueError):
            UnavailableObservation(ObservationFacet.BGP, " ")


class TestFabricOperationalState:
    def test_node_lookup_and_consistency(self) -> None:
        leaf = _router()
        fabric = FabricOperationalState("run-1", NOW, (leaf,))
        assert fabric.node("leaf-1") is leaf
        with pytest.raises(KeyError):
            fabric.node("missing")

    def test_rejects_mismatched_timestamp_or_collection_id(self) -> None:
        with pytest.raises(ValueError, match="collected_at"):
            FabricOperationalState("run-1", NOW + timedelta(seconds=1), (_router(),))
        with pytest.raises(ValueError, match="collection_id"):
            FabricOperationalState("run-2", NOW, (_router(),))

    def test_rejects_duplicate_nodes(self) -> None:
        with pytest.raises(ValueError):
            FabricOperationalState("run-1", NOW, (_router(), _router()))
