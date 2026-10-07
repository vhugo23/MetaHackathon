"""Pure parsers for Containerlab/FRR command output (NPE-1C3A).

Text in, normalized domain observations out. No subprocess, no file I/O, no
clock. Every parser raises ``OutputParseError`` for output it cannot interpret
(malformed JSON, wrong shape, missing required fields, a value the domain
model rejects); it never substitutes a default for a missing required value.
The collector turns a raised ``OutputParseError`` into an *unavailable*
observation, never into a failure state.

Parsed shapes (see tests/fixtures/containerlab_frr/README.md for provenance):

- routers: ``ip -j addr``, FRR ``show bgp summary json``, ``show ip route json``
- hosts (BusyBox ``ip`` has no ``-j``): ``ip -o link show``, ``ip -o addr show``,
  and ``ping`` summary output.
"""

import json
import re
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeGuard

from meta_rne.domain.operational_state import (
    BgpNeighborObservation,
    BgpObservation,
    BgpSessionState,
    InterfaceObservation,
    InterfaceOperState,
    NextHop,
    RouteObservation,
)


class OutputParseError(ValueError):
    """Command output could not be interpreted."""


def _normalizing_errors[**P, T](parser: Callable[P, T]) -> Callable[P, T]:
    """Re-raise any ``ValueError`` (JSON decode errors, ipaddress errors,
    domain validation errors) as ``OutputParseError``."""

    @wraps(parser)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return parser(*args, **kwargs)
        except OutputParseError:
            raise
        except ValueError as error:
            raise OutputParseError(f"{parser.__name__}: {error}") from error

    return wrapper


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_json(text: str, expected: type) -> Any:
    if not text.strip():
        raise OutputParseError("empty output")
    data = json.loads(text)
    if not isinstance(data, expected):
        raise OutputParseError(f"expected a JSON {expected.__name__}, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------

_OPER_STATE_BY_LINUX_NAME = {
    "UP": InterfaceOperState.UP,
    "DOWN": InterfaceOperState.DOWN,
    "LOWERLAYERDOWN": InterfaceOperState.DOWN,
}


def _oper_state(value: object) -> InterfaceOperState:
    # Linux reports UNKNOWN for e.g. the loopback; that is not "down".
    if isinstance(value, str):
        return _OPER_STATE_BY_LINUX_NAME.get(value.upper(), InterfaceOperState.UNKNOWN)
    return InterfaceOperState.UNKNOWN


@_normalizing_errors
def parse_ip_json_interfaces(text: str) -> tuple[InterfaceObservation, ...]:
    """``ip -j addr`` -> interfaces with oper state and every address."""
    interfaces: list[InterfaceObservation] = []
    for entry in _load_json(text, list):
        if not isinstance(entry, dict):
            raise OutputParseError("interface entry is not an object")
        name = entry.get("ifname")
        if not isinstance(name, str) or not name:
            raise OutputParseError("interface entry has no ifname")
        addr_info = entry.get("addr_info", [])
        if not isinstance(addr_info, list):
            raise OutputParseError(f"interface {name!r}: addr_info is not a list")
        addresses: list[str] = []
        for address in addr_info:
            local = address.get("local") if isinstance(address, dict) else None
            prefixlen = address.get("prefixlen") if isinstance(address, dict) else None
            if not isinstance(local, str) or not _is_int(prefixlen):
                raise OutputParseError(f"interface {name!r}: malformed addr_info entry")
            addresses.append(f"{local}/{prefixlen}")
        interfaces.append(
            InterfaceObservation(
                name=name,
                oper_state=_oper_state(entry.get("operstate")),
                addresses=tuple(addresses),
            )
        )
    return tuple(interfaces)


_LINK_LINE = re.compile(r"^\d+:\s+([^\s:@]+)(?:@\S+)?:\s+<[^>]*>")
_LINK_STATE = re.compile(r"\bstate\s+(\S+)")
_ADDR_LINE = re.compile(r"^\d+:\s+(\S+)\s+inet6?\s+(\S+)")


@_normalizing_errors
def parse_ip_oneline_interfaces(link_text: str, addr_text: str) -> tuple[InterfaceObservation, ...]:
    """BusyBox ``ip -o link show`` + ``ip -o addr show`` -> interfaces."""
    states: dict[str, InterfaceOperState] = {}
    for line in link_text.splitlines():
        if not line.strip():
            continue
        match = _LINK_LINE.match(line)
        if match is None:
            raise OutputParseError(f"unrecognized link line: {line!r}")
        state = _LINK_STATE.search(line)
        states[match.group(1)] = (
            _oper_state(state.group(1)) if state else InterfaceOperState.UNKNOWN
        )
    if not states:
        raise OutputParseError("no interfaces in ip -o link output")

    addresses: dict[str, list[str]] = {name: [] for name in states}
    for line in addr_text.splitlines():
        if not line.strip():
            continue
        match = _ADDR_LINE.match(line)
        if match is None:
            raise OutputParseError(f"unrecognized address line: {line!r}")
        name, address = match.group(1), match.group(2)
        if name not in addresses:
            raise OutputParseError(f"address for unknown interface {name!r}")
        addresses[name].append(address)

    return tuple(
        InterfaceObservation(name=name, oper_state=states[name], addresses=tuple(addresses[name]))
        for name in states
    )


# --------------------------------------------------------------------------
# BGP
# --------------------------------------------------------------------------

_BGP_STATE_BY_NAME = {state.value: state for state in BgpSessionState}
del _BGP_STATE_BY_NAME[BgpSessionState.UNKNOWN.value]


def _neighbor(neighbor_ip: str, peer: object) -> BgpNeighborObservation:
    if not isinstance(peer, dict):
        return BgpNeighborObservation(
            neighbor_ip=neighbor_ip,
            remote_as=None,
            state=BgpSessionState.UNKNOWN,
            raw_state=None,
            prefixes_received=None,
            uptime_ms=None,
        )
    raw_state = peer.get("state")
    raw_state = raw_state if isinstance(raw_state, str) else None
    state = (
        _BGP_STATE_BY_NAME.get(raw_state, BgpSessionState.UNKNOWN)
        if raw_state is not None
        else BgpSessionState.UNKNOWN
    )
    remote_as = peer.get("remoteAs")
    prefixes = peer.get("pfxRcd")
    uptime_ms = peer.get("peerUptimeMsec")
    established = state is BgpSessionState.ESTABLISHED
    return BgpNeighborObservation(
        neighbor_ip=neighbor_ip,
        remote_as=remote_as if _is_int(remote_as) and remote_as > 0 else None,
        state=state,
        raw_state=raw_state,
        prefixes_received=prefixes if established and _is_int(prefixes) and prefixes >= 0 else None,
        uptime_ms=uptime_ms if established and _is_int(uptime_ms) and uptime_ms >= 0 else None,
    )


@_normalizing_errors
def parse_bgp_summary(text: str) -> BgpObservation:
    """FRR ``show bgp summary json`` (IPv4 unicast) -> local AS, router ID and
    neighbors. A peer entry that is malformed or reports an unrecognized state
    becomes a neighbor with ``UNKNOWN`` state, never ``Idle``."""
    section = _load_json(text, dict).get("ipv4Unicast")
    if not isinstance(section, dict):
        raise OutputParseError("no ipv4Unicast section")
    local_as = section.get("as")
    router_id = section.get("routerId")
    if not _is_int(local_as) or not isinstance(router_id, str):
        raise OutputParseError("ipv4Unicast section lacks a valid as/routerId")
    peers = section.get("peers")
    if not isinstance(peers, dict):
        raise OutputParseError("ipv4Unicast section has no peers object")
    return BgpObservation(
        local_as=local_as,
        router_id=router_id,
        neighbors=tuple(_neighbor(neighbor_ip, peer) for neighbor_ip, peer in peers.items()),
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def _next_hop(raw: object) -> NextHop | None:
    """``None`` for a next-hop that is not active. FRR omits ``active`` for an
    inactive next-hop (seen mid-convergence in a degraded capture), so only
    an explicit ``active: true`` counts."""
    if not isinstance(raw, dict):
        raise OutputParseError("next-hop entry is not an object")
    if raw.get("active") is not True:
        return None
    ip = raw.get("ip")
    interface = raw.get("interfaceName")
    return NextHop(
        ip=ip if isinstance(ip, str) else None,
        interface=interface if isinstance(interface, str) else None,
    )


@_normalizing_errors
def parse_frr_routes(text: str) -> tuple[RouteObservation, ...]:
    """FRR ``show ip route json`` -> installed routes with their active
    next-hops. An entry that is not installed, or has no active next-hop, is
    not a forwarding route and is omitted. One prefix may yield several
    observations (e.g. ``local`` and ``connected``)."""
    routes: list[RouteObservation] = []
    for prefix, entries in _load_json(text, dict).items():
        if not isinstance(entries, list):
            raise OutputParseError(f"route {prefix!r}: entries is not a list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise OutputParseError(f"route {prefix!r}: entry is not an object")
            protocol = entry.get("protocol")
            if not isinstance(protocol, str) or not protocol:
                raise OutputParseError(f"route {prefix!r}: entry has no protocol")
            if entry.get("installed") is not True:
                continue
            raw_next_hops = entry.get("nexthops")
            if not isinstance(raw_next_hops, list):
                raise OutputParseError(f"route {prefix!r}: entry has no nexthops list")
            active = [hop for hop in map(_next_hop, raw_next_hops) if hop is not None]
            if not active:
                continue
            active.sort(key=lambda hop: (hop.ip or "", hop.interface or ""))
            routes.append(
                RouteObservation(prefix=prefix, protocol=protocol, next_hops=tuple(active))
            )
    return tuple(routes)


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

_PING_SUMMARY = re.compile(r"(\d+) packets transmitted, (\d+) packets received")


@_normalizing_errors
def parse_ping_summary(text: str) -> tuple[int, int]:
    """``ping`` output -> (sent, received). Total loss is a valid observation;
    output with no summary line (e.g. an exec failure) is not."""
    match = _PING_SUMMARY.search(text)
    if match is None:
        raise OutputParseError("no ping summary line")
    sent, received = int(match.group(1)), int(match.group(2))
    if sent <= 0 or received > sent:
        raise OutputParseError(f"implausible ping counts: {sent} sent, {received} received")
    return sent, received
