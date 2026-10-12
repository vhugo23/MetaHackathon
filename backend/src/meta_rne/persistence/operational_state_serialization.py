"""Backend-owned codec for ``FabricOperationalState`` (NPE-1D1A).

``state`` JSONB document, ``schema_version`` 1 (stored in its own column, not
inside the document)::

    {"collection_id": str, "collected_at": ISO-8601 UTC, "nodes": [node, ...]}

Every domain field round-trips exactly: tuples as JSON arrays (order kept),
enums by ``.value``, optional values as ``null``, ``raw_state`` verbatim, and
unavailable facets as explicit entries. Nothing is defaulted: every key is
required, every type is checked (a ``bool`` is not an ``int``), and any
malformed or unsupported input raises ``SerializationError`` — including a
value the domain constructors reject. This module deliberately shares nothing
with ``scripts/``.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

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
from meta_rne.persistence.serialization import (
    SerializationError,
    _enum,
    _get,
    _require_dict,
    _require_list,
    _utc_datetime_from_json,
)

OPERATIONAL_STATE_SCHEMA_VERSION = 1


# --- typed field readers ---------------------------------------------------


def _str(data: dict[str, Any], key: str, what: str) -> str:
    value = _get(data, key, what)
    if not isinstance(value, str):
        raise SerializationError(f"{what}.{key} must be a string, got {type(value).__name__}")
    return value


def _opt_str(data: dict[str, Any], key: str, what: str) -> str | None:
    value = _get(data, key, what)
    if value is not None and not isinstance(value, str):
        raise SerializationError(f"{what}.{key} must be a string or null")
    return value


def _int(data: dict[str, Any], key: str, what: str) -> int:
    value = _get(data, key, what)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(f"{what}.{key} must be an integer, got {type(value).__name__}")
    return value


def _opt_int(data: dict[str, Any], key: str, what: str) -> int | None:
    value = _get(data, key, what)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(f"{what}.{key} must be an integer or null")
    return value


def _str_list(data: dict[str, Any], key: str, what: str) -> tuple[str, ...]:
    items = _require_list(_get(data, key, what), f"{what}.{key}")
    for item in items:
        if not isinstance(item, str):
            raise SerializationError(f"{what}.{key} must contain only strings")
    return tuple(items)


def _objects(data: dict[str, Any], key: str, what: str) -> list[dict[str, Any]]:
    items = _require_list(_get(data, key, what), f"{what}.{key}")
    return [_require_dict(item, f"{what}.{key}[]") for item in items]


def _timestamp(data: dict[str, Any], key: str, what: str) -> datetime:
    return _utc_datetime_from_json(_get(data, key, what), f"{what}.{key}")


def _construct[T](what: str, build: Callable[[], T]) -> T:
    # Domain constructors validate (addresses, uniqueness, facet rules); a
    # rejection of persisted data is a data-integrity failure, never a leaked
    # ValueError.
    try:
        return build()
    except SerializationError:
        raise
    except (ValueError, TypeError) as exc:
        raise SerializationError(f"{what} is not a valid persisted value: {exc}") from exc


# --- encode ----------------------------------------------------------------


def _node_to_json(node: NormalizedOperationalState) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "role": node.role.value,
        "collected_at": node.collected_at.isoformat(),
        "source": {
            "collector": node.source.collector,
            "lab_name": node.source.lab_name,
            "collection_id": node.source.collection_id,
        },
        "interfaces": [
            {
                "name": interface.name,
                "oper_state": interface.oper_state.value,
                "addresses": list(interface.addresses),
            }
            for interface in node.interfaces
        ],
        "bgp": (
            None
            if node.bgp is None
            else {
                "local_as": node.bgp.local_as,
                "router_id": node.bgp.router_id,
                "neighbors": [
                    {
                        "neighbor_ip": neighbor.neighbor_ip,
                        "remote_as": neighbor.remote_as,
                        "state": neighbor.state.value,
                        "raw_state": neighbor.raw_state,
                        "prefixes_received": neighbor.prefixes_received,
                        "uptime_ms": neighbor.uptime_ms,
                    }
                    for neighbor in node.bgp.neighbors
                ],
            }
        ),
        "routes": [
            {
                "prefix": route.prefix,
                "protocol": route.protocol,
                "next_hops": [
                    {"ip": hop.ip, "interface": hop.interface} for hop in route.next_hops
                ],
            }
            for route in node.routes
        ],
        "reachability": [
            {
                "from_node": probe.from_node,
                "target": probe.target,
                "sent": probe.sent,
                "received": probe.received,
            }
            for probe in node.reachability
        ],
        "unavailable": [
            {"facet": item.facet.value, "reason": item.reason} for item in node.unavailable
        ],
    }


def fabric_to_json(fabric: FabricOperationalState) -> dict[str, Any]:
    return {
        "collection_id": fabric.collection_id,
        "collected_at": fabric.collected_at.isoformat(),
        "nodes": [_node_to_json(node) for node in fabric.nodes],
    }


# --- decode ----------------------------------------------------------------


def _interface_from_json(data: dict[str, Any]) -> InterfaceObservation:
    what = "interface"
    return _construct(
        what,
        lambda: InterfaceObservation(
            name=_str(data, "name", what),
            oper_state=_enum(InterfaceOperState, _get(data, "oper_state", what), what),
            addresses=_str_list(data, "addresses", what),
        ),
    )


def _neighbor_from_json(data: dict[str, Any]) -> BgpNeighborObservation:
    what = "bgp neighbor"
    return _construct(
        what,
        lambda: BgpNeighborObservation(
            neighbor_ip=_str(data, "neighbor_ip", what),
            remote_as=_opt_int(data, "remote_as", what),
            state=_enum(BgpSessionState, _get(data, "state", what), what),
            raw_state=_opt_str(data, "raw_state", what),
            prefixes_received=_opt_int(data, "prefixes_received", what),
            uptime_ms=_opt_int(data, "uptime_ms", what),
        ),
    )


def _bgp_from_json(raw: Any) -> BgpObservation | None:
    if raw is None:
        return None
    what = "bgp"
    data = _require_dict(raw, what)
    return _construct(
        what,
        lambda: BgpObservation(
            local_as=_int(data, "local_as", what),
            router_id=_str(data, "router_id", what),
            neighbors=tuple(_neighbor_from_json(n) for n in _objects(data, "neighbors", what)),
        ),
    )


def _next_hop_from_json(data: dict[str, Any]) -> NextHop:
    what = "next hop"
    return _construct(
        what,
        lambda: NextHop(ip=_opt_str(data, "ip", what), interface=_opt_str(data, "interface", what)),
    )


def _route_from_json(data: dict[str, Any]) -> RouteObservation:
    what = "route"
    return _construct(
        what,
        lambda: RouteObservation(
            prefix=_str(data, "prefix", what),
            protocol=_str(data, "protocol", what),
            next_hops=tuple(_next_hop_from_json(h) for h in _objects(data, "next_hops", what)),
        ),
    )


def _probe_from_json(data: dict[str, Any]) -> ReachabilityObservation:
    what = "reachability"
    return _construct(
        what,
        lambda: ReachabilityObservation(
            from_node=_str(data, "from_node", what),
            target=_str(data, "target", what),
            sent=_int(data, "sent", what),
            received=_int(data, "received", what),
        ),
    )


def _unavailable_from_json(data: dict[str, Any]) -> UnavailableObservation:
    what = "unavailable"
    return _construct(
        what,
        lambda: UnavailableObservation(
            facet=_enum(ObservationFacet, _get(data, "facet", what), what),
            reason=_str(data, "reason", what),
        ),
    )


def _source_from_json(raw: Any) -> CollectionSource:
    what = "source"
    data = _require_dict(raw, what)
    return _construct(
        what,
        lambda: CollectionSource(
            collector=_str(data, "collector", what),
            lab_name=_str(data, "lab_name", what),
            collection_id=_str(data, "collection_id", what),
        ),
    )


def _node_from_json(data: dict[str, Any]) -> NormalizedOperationalState:
    what = "node"
    return _construct(
        what,
        lambda: NormalizedOperationalState(
            node_id=_str(data, "node_id", what),
            role=_enum(NodeRole, _get(data, "role", what), what),
            collected_at=_timestamp(data, "collected_at", what),
            source=_source_from_json(_get(data, "source", what)),
            interfaces=tuple(_interface_from_json(i) for i in _objects(data, "interfaces", what)),
            bgp=_bgp_from_json(_get(data, "bgp", what)),
            routes=tuple(_route_from_json(r) for r in _objects(data, "routes", what)),
            reachability=tuple(_probe_from_json(p) for p in _objects(data, "reachability", what)),
            unavailable=tuple(
                _unavailable_from_json(u) for u in _objects(data, "unavailable", what)
            ),
        ),
    )


def fabric_from_json(schema_version: Any, document: Any) -> FabricOperationalState:
    """Decode a stored ``state`` document. An unsupported ``schema_version``
    is rejected before the document is read."""
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != OPERATIONAL_STATE_SCHEMA_VERSION
    ):
        raise SerializationError(
            f"unsupported operational state schema_version: {schema_version!r} "
            f"(supported: {OPERATIONAL_STATE_SCHEMA_VERSION})"
        )
    what = "operational state"
    data = _require_dict(document, what)
    return _construct(
        what,
        lambda: FabricOperationalState(
            collection_id=_str(data, "collection_id", what),
            collected_at=_timestamp(data, "collected_at", what),
            nodes=tuple(_node_from_json(n) for n in _objects(data, "nodes", what)),
        ),
    )
