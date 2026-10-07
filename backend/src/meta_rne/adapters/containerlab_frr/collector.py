"""Read-only Lab 1 collector (NPE-1C3A, ADR-0003).

``ContainerlabFrrCollector`` produces one ``FabricOperationalState`` per
``collect()`` from the six fixed Lab 1 containers. It never talks to the
operating system itself: every command goes through an injected
``NodeCommandRunner``, so tests replay captured fixtures with no Docker.

Safety boundary. The module defines the *complete* set of commands that may
ever be executed (``allowed_commands``), all read-only queries. Nothing in the
public surface accepts a container, command, interface, or address: the six
containers, the per-role queries, and the two ping targets are constants.
``assert_command_allowed`` is called by the collector before every execution
and is exported so the production runner can enforce the same allowlist
independently (defense in depth).

Failure semantics. A command that fails, or whose output cannot be parsed,
makes that *facet* unavailable on that node (``UnavailableObservation``); it
is never converted into a ``down``/``Idle``/zero observation. A ``ping`` that
ran and lost packets is an observed loss, not an unavailable facet.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from meta_rne.adapters.containerlab_frr.parsers import (
    OutputParseError,
    parse_bgp_summary,
    parse_frr_routes,
    parse_ip_json_interfaces,
    parse_ip_oneline_interfaces,
    parse_ping_summary,
)
from meta_rne.domain.operational_state import (
    CollectionSource,
    FabricOperationalState,
    InterfaceObservation,
    NodeRole,
    NormalizedOperationalState,
    ObservationFacet,
    ReachabilityObservation,
    UnavailableObservation,
)

COLLECTOR_NAME = "containerlab-frr"
LAB_NAME = "meta-rne-bgp"
CONTAINER_PREFIX = f"clab-{LAB_NAME}-"

ROUTER_NODES: tuple[str, ...] = ("spine-1", "spine-2", "leaf-1", "leaf-2")
HOST_NODES: tuple[str, ...] = ("host-1", "host-2")

# Each host probes the other host's address (HOST_PINGS in
# scripts/lab_failure_scenario.py; a script test keeps the two in sync).
HOST_PING_TARGETS: dict[str, str] = {"host-1": "10.1.2.10", "host-2": "10.1.1.10"}
PING_COUNT = 3
PING_WAIT_S = 2

_ROUTER_INTERFACES = ("ip", "-j", "addr")
_ROUTER_BGP = ("vtysh", "-c", "show bgp summary json")
_ROUTER_ROUTES = ("vtysh", "-c", "show ip route json")
_HOST_LINK = ("ip", "-o", "link", "show")
_HOST_ADDR = ("ip", "-o", "addr", "show")

_STDERR_REASON_LIMIT = 200


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# (container name, command argv inside it) -> result. Must not raise for an
# ordinary command failure; report it through ``returncode``.
NodeCommandRunner = Callable[[str, Sequence[str]], CommandResult]


class CommandNotAllowedError(Exception):
    """A container or command outside the fixed read-only Lab 1 allowlist was
    requested. This is a programming error, never a collection outcome."""


def container_name(node: str) -> str:
    return CONTAINER_PREFIX + node


def lab_containers() -> frozenset[str]:
    return frozenset(container_name(node) for node in (*ROUTER_NODES, *HOST_NODES))


def _ping_command(node: str) -> tuple[str, ...]:
    return ("ping", "-c", str(PING_COUNT), "-W", str(PING_WAIT_S), HOST_PING_TARGETS[node])


def allowed_commands(node: str) -> frozenset[tuple[str, ...]]:
    if node in ROUTER_NODES:
        return frozenset({_ROUTER_INTERFACES, _ROUTER_BGP, _ROUTER_ROUTES})
    if node in HOST_NODES:
        return frozenset({_HOST_LINK, _HOST_ADDR, _ping_command(node)})
    raise CommandNotAllowedError(f"not a Lab 1 node: {node!r}")


def assert_command_allowed(container: str, command: Sequence[str]) -> None:
    if container not in lab_containers():
        raise CommandNotAllowedError(f"container is not a Lab 1 node: {container!r}")
    node = container[len(CONTAINER_PREFIX) :]
    if tuple(command) not in allowed_commands(node):
        raise CommandNotAllowedError(f"command is not allowlisted for {node}: {tuple(command)!r}")


def _failure_reason(command: tuple[str, ...], result: CommandResult) -> str:
    reason = f"{' '.join(command)!r} exited {result.returncode}"
    detail = result.stderr.strip()[:_STDERR_REASON_LIMIT]
    return f"{reason}: {detail}" if detail else reason


class ContainerlabFrrCollector:
    def __init__(
        self,
        runner: NodeCommandRunner,
        clock: Callable[[], datetime],
        collection_id_factory: Callable[[], str],
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._collection_id_factory = collection_id_factory

    def collect(self) -> FabricOperationalState:
        collected_at = self._clock()
        collection_id = self._collection_id_factory()
        source = CollectionSource(
            collector=COLLECTOR_NAME, lab_name=LAB_NAME, collection_id=collection_id
        )
        nodes = tuple(
            self._collect_router(node, collected_at, source)
            if node in ROUTER_NODES
            else self._collect_host(node, collected_at, source)
            for node in (*ROUTER_NODES, *HOST_NODES)
        )
        return FabricOperationalState(
            collection_id=collection_id, collected_at=collected_at, nodes=nodes
        )

    # -- execution ---------------------------------------------------------

    def _run(self, node: str, command: tuple[str, ...]) -> CommandResult:
        container = container_name(node)
        assert_command_allowed(container, command)
        return self._runner(container, command)

    def _observe[T](
        self,
        node: str,
        facet: ObservationFacet,
        command: tuple[str, ...],
        parse: Callable[[str], T],
    ) -> tuple[T | None, UnavailableObservation | None]:
        result = self._run(node, command)
        if result.returncode != 0:
            return None, UnavailableObservation(facet, _failure_reason(command, result))
        try:
            return parse(result.stdout), None
        except OutputParseError as error:
            return None, UnavailableObservation(facet, f"unparseable output: {error}")

    # -- per node ----------------------------------------------------------

    def _collect_router(
        self, node: str, collected_at: datetime, source: CollectionSource
    ) -> NormalizedOperationalState:
        interfaces, interfaces_problem = self._observe(
            node, ObservationFacet.INTERFACES, _ROUTER_INTERFACES, parse_ip_json_interfaces
        )
        bgp, bgp_problem = self._observe(node, ObservationFacet.BGP, _ROUTER_BGP, parse_bgp_summary)
        routes, routes_problem = self._observe(
            node, ObservationFacet.ROUTES, _ROUTER_ROUTES, parse_frr_routes
        )
        return NormalizedOperationalState(
            node_id=node,
            role=NodeRole.ROUTER,
            collected_at=collected_at,
            source=source,
            interfaces=interfaces or (),
            bgp=bgp,
            routes=routes or (),
            reachability=(),
            unavailable=tuple(
                problem
                for problem in (interfaces_problem, bgp_problem, routes_problem)
                if problem is not None
            ),
        )

    def _host_interfaces(
        self, node: str
    ) -> tuple[tuple[InterfaceObservation, ...], UnavailableObservation | None]:
        link = self._run(node, _HOST_LINK)
        addr = self._run(node, _HOST_ADDR)
        for command, result in ((_HOST_LINK, link), (_HOST_ADDR, addr)):
            if result.returncode != 0:
                return (), UnavailableObservation(
                    ObservationFacet.INTERFACES, _failure_reason(command, result)
                )
        try:
            return parse_ip_oneline_interfaces(link.stdout, addr.stdout), None
        except OutputParseError as error:
            return (), UnavailableObservation(
                ObservationFacet.INTERFACES, f"unparseable output: {error}"
            )

    def _host_reachability(
        self, node: str
    ) -> tuple[tuple[ReachabilityObservation, ...], UnavailableObservation | None]:
        command = _ping_command(node)
        ping = self._run(node, command)
        try:
            # ping exits non-zero on total loss, but its summary is still a
            # valid observation; no summary means the probe did not run.
            sent, received = parse_ping_summary(ping.stdout)
        except OutputParseError as error:
            reason = (
                _failure_reason(command, ping)
                if ping.returncode != 0
                else f"unparseable output: {error}"
            )
            return (), UnavailableObservation(ObservationFacet.REACHABILITY, reason)
        observation = ReachabilityObservation(
            from_node=node, target=HOST_PING_TARGETS[node], sent=sent, received=received
        )
        return (observation,), None

    def _collect_host(
        self, node: str, collected_at: datetime, source: CollectionSource
    ) -> NormalizedOperationalState:
        interfaces, interfaces_problem = self._host_interfaces(node)
        reachability, reachability_problem = self._host_reachability(node)
        return NormalizedOperationalState(
            node_id=node,
            role=NodeRole.HOST,
            collected_at=collected_at,
            source=source,
            interfaces=interfaces,
            bgp=None,
            routes=(),
            reachability=reachability,
            unavailable=tuple(
                problem for problem in (interfaces_problem, reachability_problem) if problem
            ),
        )
