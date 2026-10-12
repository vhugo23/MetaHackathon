"""``derive_fabric_source`` (NPE-1D1A): a fabric must name exactly one lab and
one collector before it may be persisted. Nothing is guessed."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meta_rne.domain.operational_state import (
    CollectionSource,
    EmptyFabricError,
    FabricOperationalState,
    FabricSourceError,
    FabricSourceIdentity,
    MixedCollectorError,
    MixedLabNameError,
    NodeRole,
    NormalizedOperationalState,
    derive_fabric_source,
)
from operational_state_support import fixture_fabric

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _with_source(
    fabric: FabricOperationalState, index: int, **changes: str
) -> FabricOperationalState:
    nodes = list(fabric.nodes)
    node = nodes[index]
    nodes[index] = replace(node, source=replace(node.source, **changes))
    return replace(fabric, nodes=tuple(nodes))


def test_common_source__is_derived() -> None:
    assert derive_fabric_source(fixture_fabric("baseline", T0)) == FabricSourceIdentity(
        lab_name="lab1", collector="test"
    )


def test_empty_fabric__is_rejected() -> None:
    fabric = FabricOperationalState(collection_id="run-1", collected_at=T0, nodes=())

    with pytest.raises(EmptyFabricError):
        derive_fabric_source(fabric)


def test_mixed_lab_name__is_rejected_not_first_node_wins() -> None:
    fabric = _with_source(fixture_fabric("baseline", T0), 3, lab_name="lab2")

    with pytest.raises(MixedLabNameError, match="lab1.*lab2"):
        derive_fabric_source(fabric)


def test_mixed_collector__is_rejected() -> None:
    fabric = _with_source(fixture_fabric("baseline", T0), 0, collector="other")

    with pytest.raises(MixedCollectorError, match="other.*test"):
        derive_fabric_source(fabric)


def test_all_source_errors__share_one_narrow_base() -> None:
    for error in (EmptyFabricError, MixedLabNameError, MixedCollectorError):
        assert issubclass(error, FabricSourceError)
        assert issubclass(error, ValueError)


def test_domain_still_enforces_collection_id_and_collected_at_agreement() -> None:
    fabric = fixture_fabric("baseline", T0)
    node: NormalizedOperationalState = fabric.nodes[0]
    assert node.role is NodeRole.ROUTER

    with pytest.raises(ValueError, match="collection_id differs"):
        FabricOperationalState(
            collection_id="run-1",
            collected_at=T0,
            nodes=(replace(node, source=CollectionSource("test", "lab1", "other-run")),),
        )
    with pytest.raises(ValueError, match="collected_at differs"):
        FabricOperationalState(
            collection_id="run-1",
            collected_at=T0 + timedelta(seconds=1),
            nodes=(node,),
        )
