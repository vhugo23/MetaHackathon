"""DriftDetector.compare behavior (Day 9, Gate 2).

Pure detection logic: plain NormalizedConfiguration inputs in, a
DriftReport out, no I/O, no clock, no repository, no vendor branch. See
docs/architecture.md Section 8 and docs/product-spec.md FR-04/AC-05/AC-06.

Resource/ordering/value conventions confirmed for this gate (no doc defines
them beyond the {added, removed, changed} shape): resource strings are
"interface:<name>" / "acl:<name>" / "bgp_neighbor:<neighbor_ip>"; changed
entries carry the exact NormalizedInterface/NormalizedBgpNeighbor attribute
name as `field`; added/removed entries carry the resource's own identity
string as old_value (removed) or new_value (added); output preserves input
tuple order (PolicyEvaluator precedent), never re-sorted.

NormalizedAcl.entries is a nested collection, not a scalar field, and no
ACL-entry serialization contract is approved (AC-05/AC-06 require only
whole-ACL removal and an empty first-submission diff). A matched ACL
(same name in baseline and current) therefore never produces a `changed`
entry in this gate, regardless of its `entries` content — only whole-ACL
addition/removal is compared.
"""

from meta_rne.detection.drift_detector import DriftDetector
from meta_rne.domain.config import (
    AclAction,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)

HOSTNAME = "spine-01"


def _interface(**overrides: object) -> NormalizedInterface:
    defaults: dict[str, object] = {
        "name": "GigabitEthernet0/1",
        "description": None,
        "ip_address": "10.0.0.1/30",
        "mtu": None,
        "admin_state": AdminState.UP,
        "acl_in": None,
        "acl_out": None,
    }
    defaults.update(overrides)
    return NormalizedInterface(**defaults)  # type: ignore[arg-type]


def _acl_entry(**overrides: object) -> NormalizedAclEntry:
    defaults: dict[str, object] = {
        "sequence": 10,
        "action": AclAction.PERMIT,
        "protocol": "tcp",
        "source": "any",
        "destination": "any",
    }
    defaults.update(overrides)
    return NormalizedAclEntry(**defaults)  # type: ignore[arg-type]


def _acl(**overrides: object) -> NormalizedAcl:
    defaults: dict[str, object] = {
        "name": "ACL-EXTERNAL-IN",
        "entries": (_acl_entry(),),
    }
    defaults.update(overrides)
    return NormalizedAcl(**defaults)  # type: ignore[arg-type]


def _bgp_neighbor(**overrides: object) -> NormalizedBgpNeighbor:
    defaults: dict[str, object] = {
        "neighbor_ip": "10.0.0.2",
        "remote_as": 65001,
    }
    defaults.update(overrides)
    return NormalizedBgpNeighbor(**defaults)  # type: ignore[arg-type]


def _config(
    hostname: str = HOSTNAME,
    interfaces: tuple[NormalizedInterface, ...] = (),
    bgp_neighbors: tuple[NormalizedBgpNeighbor, ...] = (),
    acls: tuple[NormalizedAcl, ...] = (),
) -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=hostname,
        interfaces=interfaces,
        routing=NormalizedRouting(bgp_neighbors=bgp_neighbors),
        acls=acls,
    )


def test_drift_detector__identical_config_object__empty_report() -> None:
    config = _config(interfaces=(_interface(),), acls=(_acl(),), bgp_neighbors=(_bgp_neighbor(),))

    report = DriftDetector.compare(config, config)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_drift_detector__equal_but_distinct_configs__empty_report() -> None:
    baseline = _config(interfaces=(_interface(),), acls=(_acl(),), bgp_neighbors=(_bgp_neighbor(),))
    current = _config(interfaces=(_interface(),), acls=(_acl(),), bgp_neighbors=(_bgp_neighbor(),))

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_drift_detector__removed_acl__single_removed_entry() -> None:
    baseline = _config(acls=(_acl(),))
    current = _config(acls=())

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.changed == ()
    assert len(report.removed) == 1
    entry = report.removed[0]
    assert entry.resource == "acl:ACL-EXTERNAL-IN"
    assert entry.field is None
    assert entry.old_value == "ACL-EXTERNAL-IN"
    assert entry.new_value is None


def test_drift_detector__added_interface__single_added_entry() -> None:
    baseline = _config(interfaces=())
    current = _config(interfaces=(_interface(name="GigabitEthernet0/2"),))

    report = DriftDetector.compare(baseline, current)

    assert report.removed == ()
    assert report.changed == ()
    assert len(report.added) == 1
    entry = report.added[0]
    assert entry.resource == "interface:GigabitEthernet0/2"
    assert entry.field is None
    assert entry.old_value is None
    assert entry.new_value == "GigabitEthernet0/2"


def test_drift_detector__changed_interface_admin_state__single_changed_entry() -> None:
    baseline = _config(interfaces=(_interface(admin_state=AdminState.UP),))
    current = _config(interfaces=(_interface(admin_state=AdminState.DOWN),))

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.resource == "interface:GigabitEthernet0/1"
    assert entry.field == "admin_state"
    assert entry.old_value == "up"
    assert entry.new_value == "down"


def test_drift_detector__changed_bgp_neighbor_remote_as__single_changed_entry() -> None:
    baseline = _config(bgp_neighbors=(_bgp_neighbor(remote_as=65001),))
    current = _config(bgp_neighbors=(_bgp_neighbor(remote_as=65002),))

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.resource == "bgp_neighbor:10.0.0.2"
    assert entry.field == "remote_as"
    assert entry.old_value == "65001"
    assert entry.new_value == "65002"


def test_drift_detector__added_removed_and_changed_together__classified_correctly() -> None:
    baseline = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/1", admin_state=AdminState.UP),
            _interface(name="GigabitEthernet0/2"),
        ),
        acls=(_acl(name="ACL-EXTERNAL-IN"),),
    )
    current = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/1", admin_state=AdminState.DOWN),
            _interface(name="GigabitEthernet0/3"),
        ),
        acls=(),
    )

    report = DriftDetector.compare(baseline, current)

    assert len(report.removed) == 2
    removed_resources = {entry.resource for entry in report.removed}
    assert removed_resources == {"interface:GigabitEthernet0/2", "acl:ACL-EXTERNAL-IN"}

    assert len(report.added) == 1
    assert report.added[0].resource == "interface:GigabitEthernet0/3"

    assert len(report.changed) == 1
    assert report.changed[0].resource == "interface:GigabitEthernet0/1"
    assert report.changed[0].field == "admin_state"


def test_drift_detector__matching_by_identity_not_position__reordered_collection() -> None:
    baseline = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/1"),
            _interface(name="GigabitEthernet0/2"),
        )
    )
    current = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/2"),
            _interface(name="GigabitEthernet0/1"),
        )
    )

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_drift_detector__does_not_mutate_inputs() -> None:
    baseline = _config(interfaces=(_interface(),), acls=(_acl(),), bgp_neighbors=(_bgp_neighbor(),))
    current = _config(
        interfaces=(_interface(admin_state=AdminState.DOWN),),
        acls=(),
        bgp_neighbors=(_bgp_neighbor(remote_as=65099),),
    )
    baseline_copy = _config(
        interfaces=(_interface(),), acls=(_acl(),), bgp_neighbors=(_bgp_neighbor(),)
    )
    current_copy = _config(
        interfaces=(_interface(admin_state=AdminState.DOWN),),
        acls=(),
        bgp_neighbors=(_bgp_neighbor(remote_as=65099),),
    )

    DriftDetector.compare(baseline, current)

    assert baseline == baseline_copy
    assert current == current_copy


def test_drift_detector__deterministic_for_same_inputs() -> None:
    baseline = _config(interfaces=(_interface(),), acls=(_acl(),))
    current = _config(interfaces=(_interface(admin_state=AdminState.DOWN),), acls=())

    first = DriftDetector.compare(baseline, current)
    second = DriftDetector.compare(baseline, current)

    assert first == second


def test_drift_detector__vendor_neutral_naming__behaves_identically() -> None:
    cisco_baseline = _config(interfaces=(_interface(name="GigabitEthernet0/1"),))
    cisco_current = _config(interfaces=())
    arista_baseline = _config(interfaces=(_interface(name="Ethernet1"),))
    arista_current = _config(interfaces=())

    cisco_report = DriftDetector.compare(cisco_baseline, cisco_current)
    arista_report = DriftDetector.compare(arista_baseline, arista_current)

    assert len(cisco_report.removed) == 1
    assert len(arista_report.removed) == 1
    assert cisco_report.added == arista_report.added == ()
    assert cisco_report.changed == arista_report.changed == ()


def test_drift_detector__matched_acl_entries_differ__no_drift() -> None:
    baseline = _config(acls=(_acl(name="ACL-EXTERNAL-IN", entries=(_acl_entry(sequence=10),)),))
    current = _config(
        acls=(
            _acl(
                name="ACL-EXTERNAL-IN",
                entries=(_acl_entry(sequence=10), _acl_entry(sequence=20, source="10.0.0.0/8")),
            ),
        )
    )

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_drift_detector__removed_acl__still_uses_identity_representation() -> None:
    baseline = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current = _config(acls=())

    report = DriftDetector.compare(baseline, current)

    assert len(report.removed) == 1
    entry = report.removed[0]
    assert entry.resource == "acl:ACL-EXTERNAL-IN"
    assert entry.field is None
    assert entry.old_value == "ACL-EXTERNAL-IN"
    assert entry.new_value is None


def test_drift_detector__added_acl__uses_identity_representation() -> None:
    baseline = _config(acls=())
    current = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))

    report = DriftDetector.compare(baseline, current)

    assert len(report.added) == 1
    entry = report.added[0]
    assert entry.resource == "acl:ACL-EXTERNAL-IN"
    assert entry.field is None
    assert entry.old_value is None
    assert entry.new_value == "ACL-EXTERNAL-IN"


def test_drift_detector__added_bgp_neighbor__single_added_entry() -> None:
    baseline = _config(bgp_neighbors=())
    current = _config(bgp_neighbors=(_bgp_neighbor(neighbor_ip="10.0.0.2"),))

    report = DriftDetector.compare(baseline, current)

    assert report.removed == ()
    assert report.changed == ()
    assert len(report.added) == 1
    entry = report.added[0]
    assert entry.resource == "bgp_neighbor:10.0.0.2"
    assert entry.field is None
    assert entry.old_value is None
    assert entry.new_value == "10.0.0.2"


def test_drift_detector__removed_bgp_neighbor__single_removed_entry() -> None:
    baseline = _config(bgp_neighbors=(_bgp_neighbor(neighbor_ip="10.0.0.2"),))
    current = _config(bgp_neighbors=())

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.changed == ()
    assert len(report.removed) == 1
    entry = report.removed[0]
    assert entry.resource == "bgp_neighbor:10.0.0.2"
    assert entry.field is None
    assert entry.old_value == "10.0.0.2"
    assert entry.new_value is None


def test_drift_detector__removed_interface__uses_identity_representation() -> None:
    baseline = _config(interfaces=(_interface(name="GigabitEthernet0/1"),))
    current = _config(interfaces=())

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.changed == ()
    assert len(report.removed) == 1
    entry = report.removed[0]
    assert entry.resource == "interface:GigabitEthernet0/1"
    assert entry.field is None
    assert entry.old_value == "GigabitEthernet0/1"
    assert entry.new_value is None


def test_drift_detector__mtu_changed__uses_decimal_string_representation() -> None:
    baseline = _config(interfaces=(_interface(mtu=1500),))
    current = _config(interfaces=(_interface(mtu=9000),))

    report = DriftDetector.compare(baseline, current)

    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.field == "mtu"
    assert entry.old_value == "1500"
    assert entry.new_value == "9000"


def test_drift_detector__optional_field_none_to_value__old_value_stays_none() -> None:
    baseline = _config(interfaces=(_interface(description=None),))
    current = _config(interfaces=(_interface(description="uplink"),))

    report = DriftDetector.compare(baseline, current)

    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.field == "description"
    assert entry.old_value is None
    assert entry.new_value == "uplink"


def test_drift_detector__optional_field_value_to_none__new_value_stays_none() -> None:
    baseline = _config(interfaces=(_interface(description="uplink"),))
    current = _config(interfaces=(_interface(description=None),))

    report = DriftDetector.compare(baseline, current)

    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.field == "description"
    assert entry.old_value == "uplink"
    assert entry.new_value is None


def test_drift_detector__multiple_changed_fields__follow_declaration_order() -> None:
    baseline = _config(
        interfaces=(
            _interface(
                description=None,
                ip_address="10.0.0.1/30",
                mtu=None,
                admin_state=AdminState.UP,
                acl_in=None,
                acl_out=None,
            ),
        )
    )
    current = _config(
        interfaces=(
            _interface(
                description="uplink",
                ip_address="10.0.0.2/30",
                mtu=9000,
                admin_state=AdminState.DOWN,
                acl_in="ACL-EXTERNAL-IN",
                acl_out="ACL-EXTERNAL-OUT",
            ),
        )
    )

    report = DriftDetector.compare(baseline, current)

    assert [entry.field for entry in report.changed] == [
        "description",
        "ip_address",
        "mtu",
        "admin_state",
        "acl_in",
        "acl_out",
    ]


def test_drift_detector__hostname_only_change__ignored() -> None:
    baseline = _config(
        hostname="spine-01",
        interfaces=(_interface(),),
        acls=(_acl(),),
        bgp_neighbors=(_bgp_neighbor(),),
    )
    current = _config(
        hostname="spine-02",
        interfaces=(_interface(),),
        acls=(_acl(),),
        bgp_neighbors=(_bgp_neighbor(),),
    )

    report = DriftDetector.compare(baseline, current)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()
