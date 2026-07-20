"""Unit test for the production snapshot-ID factory default (Day 5A)."""

from uuid import UUID

from meta_rne.application.snapshot_id import default_snapshot_id_factory


def test_default_snapshot_id_factory__returns_a_unique_valid_uuid_string() -> None:
    first = default_snapshot_id_factory()
    second = default_snapshot_id_factory()

    assert UUID(first) != UUID(second)
