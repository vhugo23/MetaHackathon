import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meta_rne.adapters.containerlab_frr import (
    CommandNotAllowedError,
    CommandResult,
    ContainerlabFrrCollector,
)
from meta_rne.adapters.containerlab_frr.collector import (
    CONTAINER_PREFIX,
    HOST_NODES,
    HOST_PING_TARGETS,
    ROUTER_NODES,
    allowed_commands,
    assert_command_allowed,
    container_name,
    lab_containers,
)
from meta_rne.domain.operational_state import (
    BgpSessionState,
    FabricOperationalState,
    InterfaceOperState,
    NextHop,
    NodeRole,
    ObservationFacet,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

_FILE_FOR_COMMAND = {
    ("ip", "-j", "addr"): "ip_j_addr.json",
    ("vtysh", "-c", "show bgp summary json"): "show_bgp_summary.json",
    ("vtysh", "-c", "show ip route json"): "show_ip_route.json",
    ("ip", "-o", "link", "show"): "ip_o_link.txt",
    ("ip", "-o", "addr", "show"): "ip_o_addr.txt",
}


class FixtureRunner:
    """Replays captured output, records every call, and can override or fail
    individual (node, command) pairs."""

    def __init__(self, root: Path, fixture_set: str) -> None:
        self._root = root / fixture_set
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.overrides: dict[tuple[str, tuple[str, ...]], CommandResult] = {}

    def __call__(self, container: str, command: Sequence[str]) -> CommandResult:
        command = tuple(command)
        self.calls.append((container, command))
        if (container, command) in self.overrides:
            return self.overrides[(container, command)]
        node = container.removeprefix(CONTAINER_PREFIX)
        filename = f"ping_{command[-1]}.txt" if command[0] == "ping" else _FILE_FOR_COMMAND[command]
        return CommandResult(0, (self._root / node / filename).read_text(encoding="utf-8"))


def _collector(
    runner: FixtureRunner, clock: Callable[[], datetime] = lambda: NOW
) -> ContainerlabFrrCollector:
    return ContainerlabFrrCollector(runner, clock, lambda: "run-1")


@pytest.fixture
def baseline_runner(containerlab_fixture_root: Path) -> FixtureRunner:
    return FixtureRunner(containerlab_fixture_root, "baseline")


@pytest.fixture
def degraded_runner(containerlab_fixture_root: Path) -> FixtureRunner:
    return FixtureRunner(containerlab_fixture_root, "degraded-active")


BGP = ("vtysh", "-c", "show bgp summary json")
ADDR_J = ("ip", "-j", "addr")
ROUTES = ("vtysh", "-c", "show ip route json")


class TestBaselineSnapshot:
    def test_six_nodes_sharing_timestamp_and_collection_id(
        self, baseline_runner: FixtureRunner
    ) -> None:
        fabric = _collector(baseline_runner).collect()
        assert [n.node_id for n in fabric.nodes] == [*ROUTER_NODES, *HOST_NODES]
        assert fabric.collection_id == "run-1"
        assert fabric.collected_at == NOW
        for node in fabric.nodes:
            assert node.collected_at == NOW
            assert node.source.collection_id == "run-1"
            assert node.source.collector == "containerlab-frr"
            assert node.source.lab_name == "meta-rne-bgp"
            assert node.unavailable == ()
        assert [fabric.node(n).role for n in ROUTER_NODES] == [NodeRole.ROUTER] * 4
        assert [fabric.node(n).role for n in HOST_NODES] == [NodeRole.HOST] * 2

    def test_four_of_four_established_with_expected_asns(
        self, baseline_runner: FixtureRunner
    ) -> None:
        fabric = _collector(baseline_runner).collect()
        local_as = {n: fabric.node(n).bgp.local_as for n in ROUTER_NODES}  # type: ignore[union-attr]
        assert local_as == {"spine-1": 65000, "spine-2": 65000, "leaf-1": 65101, "leaf-2": 65102}
        leaf_sessions = [
            neighbor
            for leaf in ("leaf-1", "leaf-2")
            for neighbor in fabric.node(leaf).bgp.neighbors  # type: ignore[union-attr]
        ]
        assert len(leaf_sessions) == 4
        assert all(s.is_established and s.remote_as == 65000 for s in leaf_sessions)
        for spine in ("spine-1", "spine-2"):
            bgp = fabric.node(spine).bgp
            assert bgp is not None and bgp.established_count == 2

    def test_two_path_ecmp_on_both_leaves(self, baseline_runner: FixtureRunner) -> None:
        fabric = _collector(baseline_runner).collect()
        expected = {
            "leaf-1": (
                "10.1.2.0/24",
                {NextHop("10.255.0.0", "eth1"), NextHop("10.255.0.2", "eth2")},
            ),
            "leaf-2": (
                "10.1.1.0/24",
                {NextHop("10.255.0.4", "eth1"), NextHop("10.255.0.6", "eth2")},
            ),
        }
        for leaf, (prefix, hops) in expected.items():
            (route,) = [r for r in fabric.node(leaf).routes if r.prefix == prefix]
            assert route.is_ecmp
            assert route.ecmp_path_count == 2
            assert set(route.next_hops) == hops

    def test_router_interface_addressing(self, baseline_runner: FixtureRunner) -> None:
        fabric = _collector(baseline_runner).collect()
        expected = {
            "spine-1": {"eth1": "10.255.0.0/31", "eth2": "10.255.0.4/31"},
            "spine-2": {"eth1": "10.255.0.2/31", "eth2": "10.255.0.6/31"},
            "leaf-1": {"eth1": "10.255.0.1/31", "eth2": "10.255.0.3/31", "eth3": "10.1.1.1/24"},
            "leaf-2": {"eth1": "10.255.0.5/31", "eth2": "10.255.0.7/31", "eth3": "10.1.2.1/24"},
        }
        for node, interfaces in expected.items():
            observed = {i.name: i for i in fabric.node(node).interfaces}
            for name, address in interfaces.items():
                assert observed[name].oper_state is InterfaceOperState.UP
                assert address in observed[name].addresses

    def test_hosts_and_reachability(self, baseline_runner: FixtureRunner) -> None:
        fabric = _collector(baseline_runner).collect()
        for host, address in (("host-1", "10.1.1.10/24"), ("host-2", "10.1.2.10/24")):
            state = fabric.node(host)
            eth1 = next(i for i in state.interfaces if i.name == "eth1")
            assert address in eth1.addresses
            (probe,) = state.reachability
            assert probe.from_node == host
            assert probe.target == HOST_PING_TARGETS[host]
            assert probe.succeeded and probe.sent == 3
            assert state.bgp is None
            assert state.routes == ()
        assert all(fabric.node(r).reachability == () for r in ROUTER_NODES)

    def test_repeatable(self, baseline_runner: FixtureRunner) -> None:
        assert _collector(baseline_runner).collect() == _collector(baseline_runner).collect()


class TestDegradedSnapshot:
    def test_failed_session_is_an_observed_active_state(
        self, degraded_runner: FixtureRunner
    ) -> None:
        fabric = _collector(degraded_runner).collect()
        leaf1 = {n.neighbor_ip: n for n in fabric.node("leaf-1").bgp.neighbors}  # type: ignore[union-attr]
        assert leaf1["10.255.0.0"].state is BgpSessionState.ACTIVE
        assert leaf1["10.255.0.2"].is_established
        spine1 = {n.neighbor_ip: n for n in fabric.node("spine-1").bgp.neighbors}  # type: ignore[union-attr]
        assert spine1["10.255.0.1"].state is BgpSessionState.ACTIVE
        assert spine1["10.255.0.5"].is_established
        assert fabric.node("spine-2").bgp.established_count == 2  # type: ignore[union-attr]
        assert fabric.node("leaf-2").bgp.established_count == 2  # type: ignore[union-attr]
        # An observed failure is not a collection failure.
        assert all(node.unavailable == () for node in fabric.nodes)

    def test_interface_down_and_single_path_on_both_leaves(
        self, degraded_runner: FixtureRunner
    ) -> None:
        fabric = _collector(degraded_runner).collect()
        eth1 = next(i for i in fabric.node("leaf-1").interfaces if i.name == "eth1")
        assert eth1.oper_state is InterfaceOperState.DOWN
        (leaf1,) = [r for r in fabric.node("leaf-1").routes if r.prefix == "10.1.2.0/24"]
        (leaf2,) = [r for r in fabric.node("leaf-2").routes if r.prefix == "10.1.1.0/24"]
        assert leaf1.next_hops == (NextHop("10.255.0.2", "eth2"),)
        assert leaf2.next_hops == (NextHop("10.255.0.6", "eth2"),)
        assert not leaf1.is_ecmp and not leaf2.is_ecmp

    def test_reachability_survives(self, degraded_runner: FixtureRunner) -> None:
        fabric = _collector(degraded_runner).collect()
        assert all(fabric.node(h).reachability[0].succeeded for h in HOST_NODES)


class TestFailureSemantics:
    def test_failed_bgp_command_is_unavailable_not_down(
        self, baseline_runner: FixtureRunner
    ) -> None:
        baseline_runner.overrides[(container_name("leaf-1"), BGP)] = CommandResult(
            1, "", "Error response from daemon: boom"
        )
        leaf1 = _collector(baseline_runner).collect().node("leaf-1")
        assert leaf1.bgp is None
        assert leaf1.is_unavailable(ObservationFacet.BGP)
        (problem,) = leaf1.unavailable
        assert "exited 1" in problem.reason and "boom" in problem.reason
        # Other facets on the same node are still collected.
        assert leaf1.interfaces and leaf1.routes

    def test_malformed_json_is_unavailable_not_down(self, baseline_runner: FixtureRunner) -> None:
        baseline_runner.overrides[(container_name("spine-1"), BGP)] = CommandResult(0, "{oops")
        spine1 = _collector(baseline_runner).collect().node("spine-1")
        assert spine1.bgp is None
        (problem,) = spine1.unavailable
        assert problem.facet is ObservationFacet.BGP
        assert problem.reason.startswith("unparseable output")

    def test_empty_output_with_success_status_is_unavailable(
        self, baseline_runner: FixtureRunner
    ) -> None:
        baseline_runner.overrides[(container_name("leaf-2"), ROUTES)] = CommandResult(0, "")
        leaf2 = _collector(baseline_runner).collect().node("leaf-2")
        assert leaf2.routes == ()
        assert leaf2.is_unavailable(ObservationFacet.ROUTES)

    def test_missing_container_makes_every_facet_unavailable(
        self, baseline_runner: FixtureRunner
    ) -> None:
        container = container_name("leaf-1")
        for command in allowed_commands("leaf-1"):
            baseline_runner.overrides[(container, command)] = CommandResult(
                1, "", f"Error response from daemon: No such container: {container}"
            )
        leaf1 = _collector(baseline_runner).collect().node("leaf-1")
        assert {u.facet for u in leaf1.unavailable} == {
            ObservationFacet.INTERFACES,
            ObservationFacet.BGP,
            ObservationFacet.ROUTES,
        }
        assert (leaf1.interfaces, leaf1.bgp, leaf1.routes) == ((), None, ())

    def test_unavailable_is_never_reported_as_a_failure_state(
        self, baseline_runner: FixtureRunner
    ) -> None:
        for node in ROUTER_NODES:
            for command in allowed_commands(node):
                baseline_runner.overrides[(container_name(node), command)] = CommandResult(125)
        fabric = _collector(baseline_runner).collect()
        for node in ROUTER_NODES:
            state = fabric.node(node)
            assert state.bgp is None
            assert state.interfaces == ()
            assert state.routes == ()
            assert len(state.unavailable) == 3
        # Hosts are untouched and still healthy.
        assert all(fabric.node(h).reachability[0].succeeded for h in HOST_NODES)

    def test_unrecognized_neighbor_state_is_unknown_not_idle(
        self, containerlab_fixture_root: Path
    ) -> None:
        runner = FixtureRunner(containerlab_fixture_root, "degraded-clearing")
        # degraded-clearing has leaf-1/spine-1 only: serve the rest from baseline.
        baseline = FixtureRunner(containerlab_fixture_root, "baseline")

        def mixed(container: str, command: Sequence[str]) -> CommandResult:
            node = container.removeprefix(CONTAINER_PREFIX)
            return (runner if node in ("leaf-1", "spine-1") else baseline)(container, command)

        fabric = ContainerlabFrrCollector(mixed, lambda: NOW, lambda: "run-1").collect()
        transient = {n.neighbor_ip: n for n in fabric.node("leaf-1").bgp.neighbors}["10.255.0.0"]  # type: ignore[union-attr]
        assert transient.state is BgpSessionState.UNKNOWN
        assert transient.state not in (BgpSessionState.IDLE, BgpSessionState.ACTIVE)
        assert transient.raw_state == "Clearing"
        assert fabric.node("leaf-1").unavailable == ()

    def test_ping_total_loss_is_observed_loss(self, baseline_runner: FixtureRunner) -> None:
        loss = (
            "--- 10.1.2.10 ping statistics ---\n"
            "3 packets transmitted, 0 packets received, 100% packet loss\n"
        )
        command = ("ping", "-c", "3", "-W", "2", "10.1.2.10")
        baseline_runner.overrides[(container_name("host-1"), command)] = CommandResult(1, loss)
        host1 = _collector(baseline_runner).collect().node("host-1")
        (probe,) = host1.reachability
        assert (probe.sent, probe.received, probe.succeeded) == (3, 0, False)
        assert host1.unavailable == ()

    def test_ping_exec_failure_is_unavailable_not_loss(
        self, baseline_runner: FixtureRunner
    ) -> None:
        command = ("ping", "-c", "3", "-W", "2", "10.1.2.10")
        baseline_runner.overrides[(container_name("host-1"), command)] = CommandResult(
            1, "", "Error response from daemon: container is not running"
        )
        host1 = _collector(baseline_runner).collect().node("host-1")
        assert host1.reachability == ()
        (problem,) = host1.unavailable
        assert problem.facet is ObservationFacet.REACHABILITY
        assert "exited 1" in problem.reason

    def test_ping_unparseable_success_is_unavailable(self, baseline_runner: FixtureRunner) -> None:
        command = ("ping", "-c", "3", "-W", "2", "10.1.2.10")
        baseline_runner.overrides[(container_name("host-1"), command)] = CommandResult(0, "???")
        host1 = _collector(baseline_runner).collect().node("host-1")
        assert host1.reachability == ()
        assert host1.unavailable[0].reason.startswith("unparseable output")

    def test_host_interface_command_failure_is_unavailable(
        self, baseline_runner: FixtureRunner
    ) -> None:
        baseline_runner.overrides[(container_name("host-2"), ("ip", "-o", "link", "show"))] = (
            CommandResult(1, "", "nope")
        )
        host2 = _collector(baseline_runner).collect().node("host-2")
        assert host2.interfaces == ()
        assert host2.is_unavailable(ObservationFacet.INTERFACES)
        assert host2.reachability[0].succeeded

    def test_host_interface_garbage_is_unavailable(self, baseline_runner: FixtureRunner) -> None:
        baseline_runner.overrides[(container_name("host-2"), ("ip", "-o", "addr", "show"))] = (
            CommandResult(0, "garbage")
        )
        host2 = _collector(baseline_runner).collect().node("host-2")
        assert host2.is_unavailable(ObservationFacet.INTERFACES)

    def test_naive_clock_is_rejected(self, baseline_runner: FixtureRunner) -> None:
        with pytest.raises(ValueError):
            _collector(baseline_runner, clock=lambda: datetime(2026, 10, 7)).collect()


class TestSafetyBoundary:
    def test_exactly_the_six_lab_containers(self) -> None:
        assert lab_containers() == frozenset(
            f"clab-meta-rne-bgp-{node}"
            for node in ("spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2")
        )
        assert len(lab_containers()) == 6

    def test_collection_executes_only_allowlisted_commands_on_lab_containers(
        self, baseline_runner: FixtureRunner
    ) -> None:
        _collector(baseline_runner).collect()
        assert len(baseline_runner.calls) == 18  # 4 routers x 3 + 2 hosts x 3
        assert {c for c, _ in baseline_runner.calls} == lab_containers()
        for container, command in baseline_runner.calls:
            assert_command_allowed(container, command)

    def test_allowlist_contains_no_mutating_command(self) -> None:
        mutating = {
            "set",
            "add",
            "del",
            "delete",
            "replace",
            "flush",
            "configure",
            "conf",
            "write",
            "clear",
            "kill",
        }
        for node in (*ROUTER_NODES, *HOST_NODES):
            for command in allowed_commands(node):
                joined = " ".join(command).lower()
                assert not mutating & set(joined.replace('"', " ").split()), command
                assert "configure" not in joined and "link set" not in joined

    def test_router_allowlist_is_exact(self) -> None:
        assert allowed_commands("leaf-1") == frozenset({ADDR_J, BGP, ROUTES})

    def test_host_allowlist_is_exact_and_pings_only_the_fixed_target(self) -> None:
        assert allowed_commands("host-1") == frozenset(
            {
                ("ip", "-o", "link", "show"),
                ("ip", "-o", "addr", "show"),
                ("ping", "-c", "3", "-W", "2", "10.1.2.10"),
            }
        )
        assert ("ping", "-c", "3", "-W", "2", "10.1.1.10") not in allowed_commands("host-1")

    @pytest.mark.parametrize(
        ("container", "command"),
        [
            ("clab-meta-rne-bgp-leaf-1", ("ip", "link", "set", "eth1", "down")),
            ("clab-meta-rne-bgp-leaf-1", ("ip", "link", "set", "eth1", "up")),
            ("clab-meta-rne-bgp-leaf-1", ("vtysh", "-c", "configure terminal")),
            ("clab-meta-rne-bgp-leaf-1", ("vtysh", "-c", "clear bgp *")),
            ("clab-meta-rne-bgp-leaf-1", ("vtysh", "-c", "show running-config")),
            ("clab-meta-rne-bgp-leaf-1", ("sh", "-c", "ip -j addr")),
            ("clab-meta-rne-bgp-leaf-1", ("ip", "-j", "addr", "show", "eth1")),
            ("clab-meta-rne-bgp-leaf-1", ()),
            ("clab-meta-rne-bgp-leaf-1", ("ping", "-c", "3", "-W", "2", "10.1.2.10")),
            ("clab-meta-rne-bgp-host-1", ("vtysh", "-c", "show bgp summary json")),
            ("clab-meta-rne-bgp-host-1", ("ping", "-c", "3", "-W", "2", "8.8.8.8")),
            ("clab-meta-rne-bgp-host-1", ("ping", "-c", "3", "-W", "2", "10.1.1.10")),
            ("clab-meta-rne-bgp-host-1", ("ping", "-c", "1000", "-W", "2", "10.1.2.10")),
        ],
    )
    def test_arbitrary_command_rejected(self, container: str, command: tuple[str, ...]) -> None:
        with pytest.raises(CommandNotAllowedError):
            assert_command_allowed(container, command)

    @pytest.mark.parametrize(
        "container",
        [
            "",
            "leaf-1",
            "clab-meta-rne-bgp-leaf-3",
            "clab-meta-rne-bgp-leaf-1;id",
            "clab-meta-rne-bgp-leaf-1 ",
            "clab-other-leaf-1",
            "clab-meta-rne-bgp-",
            "meta-rne-postgres",
            "clab-meta-rne-bgp-mgmt",
        ],
    )
    def test_arbitrary_container_rejected(self, container: str) -> None:
        with pytest.raises(CommandNotAllowedError):
            assert_command_allowed(container, ("ip", "-j", "addr"))

    def test_unknown_node_has_no_allowlist(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            allowed_commands("leaf-9")

    def test_public_api_exposes_no_container_command_or_target_argument(self) -> None:
        assert list(inspect.signature(ContainerlabFrrCollector.collect).parameters) == ["self"]
        assert list(inspect.signature(ContainerlabFrrCollector.__init__).parameters) == [
            "self",
            "runner",
            "clock",
            "collection_id_factory",
        ]

    def test_adapter_modules_import_no_process_or_io_modules(self) -> None:
        import ast

        import meta_rne.adapters.containerlab_frr.collector as collector_module
        import meta_rne.adapters.containerlab_frr.parsers as parsers_module

        forbidden = {"subprocess", "os", "socket", "shutil", "pathlib", "urllib", "http"}
        for module in (collector_module, parsers_module):
            imported: set[str] = set()
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            assert not forbidden & imported, module.__name__


def test_collect_returns_a_fabric_state(baseline_runner: FixtureRunner) -> None:
    assert isinstance(_collector(baseline_runner).collect(), FabricOperationalState)
