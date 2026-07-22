"""DriftReport/DriftEntry domain value object invariants (Day 9, Gate 1).

Pure construction-time invariants only. `DriftDetector.compare` (the
detection-layer producer of these values) is Gate 2 — see
docs/architecture.md Section 8 and docs/domain-model.md's `DriftDetector`
port signature.
"""

import pytest

from meta_rne.domain import DriftEntry, DriftReport


def test_drift_entry__valid_fields__constructs_successfully() -> None:
    entry = DriftEntry(
        resource="acl:ACL-EXTERNAL-IN",
        field=None,
        old_value="ACL-EXTERNAL-IN",
        new_value=None,
    )

    assert entry.resource == "acl:ACL-EXTERNAL-IN"
    assert entry.field is None
    assert entry.old_value == "ACL-EXTERNAL-IN"
    assert entry.new_value is None


def test_drift_entry__empty_resource__raises_value_error() -> None:
    with pytest.raises(ValueError, match="resource"):
        DriftEntry(resource="", field=None, old_value="x", new_value=None)


def test_drift_entry__both_values_none__raises_value_error() -> None:
    with pytest.raises(ValueError, match="old_value.*new_value"):
        DriftEntry(resource="acl:ACL-EXTERNAL-IN", field=None, old_value=None, new_value=None)


def test_drift_entry__is_immutable() -> None:
    entry = DriftEntry(resource="acl:ACL-EXTERNAL-IN", field=None, old_value="x", new_value=None)

    with pytest.raises(AttributeError):
        entry.resource = "acl:OTHER"  # type: ignore[misc]


def test_drift_entry__equal_fields__are_equal() -> None:
    first = DriftEntry(resource="acl:ACL-EXTERNAL-IN", field=None, old_value="x", new_value=None)
    second = DriftEntry(resource="acl:ACL-EXTERNAL-IN", field=None, old_value="x", new_value=None)

    assert first == second


def test_drift_report__valid_fields__constructs_successfully() -> None:
    removed = DriftEntry(
        resource="acl:ACL-EXTERNAL-IN", field=None, old_value="ACL-EXTERNAL-IN", new_value=None
    )

    report = DriftReport(added=(), removed=(removed,), changed=())

    assert report.added == ()
    assert report.removed == (removed,)
    assert report.changed == ()


def test_drift_report__no_changes__constructs_with_empty_tuples() -> None:
    report = DriftReport(added=(), removed=(), changed=())

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_drift_report__is_immutable() -> None:
    report = DriftReport(added=(), removed=(), changed=())

    with pytest.raises(AttributeError):
        report.added = ()  # type: ignore[misc]


def test_drift_report__equal_fields__are_equal() -> None:
    first = DriftReport(added=(), removed=(), changed=())
    second = DriftReport(added=(), removed=(), changed=())

    assert first == second
