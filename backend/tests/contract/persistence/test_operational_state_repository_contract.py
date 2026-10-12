"""OperationalStateRepository contract (NPE-1D1A), run against the in-memory
and the PostgreSQL implementations so neither can be looser than the other."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from meta_rne.domain.operational_state import (
    BgpSessionState,
    EmptyFabricError,
    FabricOperationalState,
    MixedCollectorError,
    MixedLabNameError,
    OperationalStateSaveOutcome,
)
from meta_rne.persistence.errors import (
    OperationalStateConflictError,
    PersistenceConflictError,
    StaleOperationalStateError,
)
from operational_state_support import fixture_fabric, rich_fixture_fabric, with_neighbor_states

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=30)
T2 = T0 + timedelta(seconds=60)

CREATED = OperationalStateSaveOutcome.CREATED
REPLACED = OperationalStateSaveOutcome.REPLACED
UNCHANGED = OperationalStateSaveOutcome.UNCHANGED


def _fabric(
    collection_id: str = "run-1", at: datetime = T0, fixture_set: str = "baseline"
) -> FabricOperationalState:
    return rich_fixture_fabric(fixture_set, at, collection_id)


def test_get_latest__absent_lab_returns_none(repositories: SimpleNamespace) -> None:
    assert repositories.operational_state.get_latest("lab1") is None


def test_initial_save__creates_and_get_latest_returns_the_exact_fabric(
    repositories: SimpleNamespace,
) -> None:
    repo = repositories.operational_state
    fabric = _fabric()

    assert repo.save_latest(fabric) is CREATED

    assert repo.get_latest("lab1") == fabric
    assert repo.get_latest("lab2") is None


def test_newer_distinct_collection__replaces(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    repo.save_latest(_fabric("run-1", T0))
    newer = _fabric("run-2", T1, "degraded-active")

    assert repo.save_latest(newer) is REPLACED

    assert repo.get_latest("lab1") == newer


def test_collection_id_is_opaque__a_lexically_smaller_id_can_be_newer(
    repositories: SimpleNamespace,
) -> None:
    repo = repositories.operational_state
    repo.save_latest(_fabric("zzz-last-alphabetically", T0))
    newer = _fabric("aaa-first-alphabetically", T1)

    assert repo.save_latest(newer) is REPLACED

    assert repo.get_latest("lab1") == newer


def test_exact_replay__is_unchanged(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    fabric = _fabric()
    repo.save_latest(fabric)

    assert repo.save_latest(_fabric()) is UNCHANGED

    assert repo.get_latest("lab1") == fabric


def test_older_distinct_collection__is_stale_and_changes_nothing(
    repositories: SimpleNamespace,
) -> None:
    repo = repositories.operational_state
    current = _fabric("run-2", T1)
    repo.save_latest(current)

    with pytest.raises(StaleOperationalStateError) as excinfo:
        repo.save_latest(_fabric("run-1", T0, "degraded-active"))

    assert excinfo.value.lab_name == "lab1"
    assert excinfo.value.collection_id == "run-1"
    assert excinfo.value.stored_collection_id == "run-2"
    assert isinstance(excinfo.value, PersistenceConflictError)
    assert repo.get_latest("lab1") == current


def test_equal_timestamp_different_id__conflicts(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    current = _fabric("run-1", T0)
    repo.save_latest(current)

    with pytest.raises(OperationalStateConflictError, match="identical collected_at"):
        repo.save_latest(_fabric("run-2", T0, "degraded-active"))

    assert repo.get_latest("lab1") == current


@pytest.mark.parametrize("at", [T1, T0 - timedelta(seconds=1)], ids=["newer", "older"])
def test_same_id_different_timestamp__conflicts(
    repositories: SimpleNamespace, at: datetime
) -> None:
    repo = repositories.operational_state
    current = _fabric("run-1", T0)
    repo.save_latest(current)

    with pytest.raises(OperationalStateConflictError, match="different collected_at") as excinfo:
        repo.save_latest(_fabric("run-1", at))

    assert not isinstance(excinfo.value, StaleOperationalStateError)
    assert repo.get_latest("lab1") == current


def test_same_id_same_timestamp_different_payload__conflicts(
    repositories: SimpleNamespace,
) -> None:
    repo = repositories.operational_state
    current = _fabric("run-1", T0, "baseline")
    repo.save_latest(current)

    with pytest.raises(OperationalStateConflictError, match="different state"):
        repo.save_latest(_fabric("run-1", T0, "degraded-active"))

    assert repo.get_latest("lab1") == current


def test_labs_are_independent(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    lab1 = _fabric("run-1", T1)
    other = rich_fixture_fabric("baseline", T0, "run-1", lab_name="lab2")

    assert repo.save_latest(lab1) is CREATED
    # Older than lab1's state, but a different lab and the same collection_id.
    assert repo.save_latest(other) is CREATED

    assert repo.get_latest("lab1") == lab1
    assert repo.get_latest("lab2") == other


def test_empty_fabric__is_rejected_and_nothing_is_written(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state

    with pytest.raises(EmptyFabricError):
        repo.save_latest(FabricOperationalState(collection_id="run-1", collected_at=T0, nodes=()))


def test_mixed_lab_or_collector__is_rejected(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    base = fixture_fabric("baseline", T0)
    nodes = list(base.nodes)
    nodes[1] = replace(nodes[1], source=replace(nodes[1].source, lab_name="lab2"))
    with pytest.raises(MixedLabNameError):
        repo.save_latest(replace(base, nodes=tuple(nodes)))

    nodes = list(base.nodes)
    nodes[1] = replace(nodes[1], source=replace(nodes[1].source, collector="other"))
    with pytest.raises(MixedCollectorError):
        repo.save_latest(replace(base, nodes=tuple(nodes)))

    assert repo.get_latest("lab1") is None
    assert repo.get_latest("lab2") is None


def test_replacement_is_a_whole_document_swap(repositories: SimpleNamespace) -> None:
    repo = repositories.operational_state
    repo.save_latest(_fabric("run-1", T0, "baseline"))
    degraded = with_neighbor_states(
        fixture_fabric("baseline", T1, "run-2"),
        {("leaf-1", "10.255.0.0"): BgpSessionState.IDLE},
    )

    repo.save_latest(degraded)

    stored = repo.get_latest("lab1")
    assert stored == degraded
    assert [n.node_id for n in stored.nodes] == [n.node_id for n in degraded.nodes]
