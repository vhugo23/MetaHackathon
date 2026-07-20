"""Unit tests for ``ConfigIngestionService`` (Day 5A) against a real
``InMemoryUnitOfWork`` — never a mocked repository when the in-memory double
can prove the behavior (Day 5A plan item 12).

The very first test below
(``test_config_ingestion_service__new_device_satisfied_policy__returns_zero_incidents``)
was written and run against a codebase with no ``ConfigIngestionService`` at
all, producing a real ``ModuleNotFoundError`` before the service existed —
genuine red-green-refactor, not retrofitted tests.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.errors import ConfigurationParseError
from meta_rne.application.models import IngestConfigurationCommand
from meta_rne.detection.incident_factory import IncidentFactory
from meta_rne.domain.config import (
    AclDirection,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.errors import ParseErrorCode, UnsupportedVendorError
from meta_rne.domain.incident import IncidentSource
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.domain.snapshot import compute_raw_text_hash
from meta_rne.persistence.errors import DeviceConflictError, SnapshotAlreadyExistsError
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)

_SATISFIED_RAW_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "ip access-list extended ACL-EXTERNAL-IN\n"
    " 10 permit ip any any\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " ip access-group ACL-EXTERNAL-IN in\n"
    "!\n"
)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"

_MULTI_VIOLATION_RAW_CONFIG = (
    "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\ninterface GigabitEthernet0/2\n!\n"
)

_NO_HOSTNAME_RAW_CONFIG = "interface GigabitEthernet0/1\n!\n"


def _policy_a() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-a-acl-external-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-EXTERNAL-IN",
                interface_name="GigabitEthernet0/1",
                direction=AclDirection.IN,
                severity=Severity.MEDIUM,
                recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
            ),
        ),
        created_at=T0,
    )


def _policy_b() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-b-acl-mgmt-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-MGMT-IN",
                interface_name="GigabitEthernet0/2",
                direction=AclDirection.IN,
                severity=Severity.HIGH,
                recommendation="Assign ACL-MGMT-IN inbound to GigabitEthernet0/2",
            ),
        ),
        created_at=T0,
    )


def _command(**overrides: object) -> IngestConfigurationCommand:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "vendor": "cisco-ios-xe",
        "raw_config_text": _SATISFIED_RAW_CONFIG,
        "observed_at": T0,
    }
    defaults.update(overrides)
    return IngestConfigurationCommand(**defaults)  # type: ignore[arg-type]


class _FakeAristaAdapter:
    """Trivial second adapter — Day 5A plan item 6's vendor-mismatch test
    needs a real second registered vendor, not a mock of CiscoAdapter."""

    vendor_id: str = VendorType.ARISTA_EOS

    def parse(self, raw_text: str) -> NormalizedConfiguration:
        return NormalizedConfiguration(
            hostname="arista-1",
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        )


class _SpyAdapter:
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.vendor_id = wrapped.vendor_id
        self.calls: list[str] = []

    def parse(self, raw_text: str) -> Any:
        self.calls.append(raw_text)
        return self._wrapped.parse(raw_text)


class _CountingFactory:
    def __init__(self, inner: Callable[[], Any]) -> None:
        self._inner = inner
        self.call_count = 0

    def __call__(self) -> Any:
        self.call_count += 1
        return self._inner()


@dataclass
class _LifecycleCounts:
    commit: int = 0
    rollback: int = 0
    close: int = 0


@dataclass
class _LifecycleSpyUnitOfWork:
    _wrapped: Any
    _counts: _LifecycleCounts
    _fail_commit: Exception | None = None
    _fail_rollback: Exception | None = None
    _fail_close: Exception | None = None
    devices: Any = field(init=False)
    configuration_snapshots: Any = field(init=False)
    configuration_policies: Any = field(init=False)
    incidents: Any = field(init=False)

    def __post_init__(self) -> None:
        self.devices = self._wrapped.devices
        self.configuration_snapshots = self._wrapped.configuration_snapshots
        self.configuration_policies = self._wrapped.configuration_policies
        self.incidents = self._wrapped.incidents

    def commit(self) -> None:
        self._counts.commit += 1
        if self._fail_commit is not None:
            raise self._fail_commit
        self._wrapped.commit()

    def rollback(self) -> None:
        self._counts.rollback += 1
        if self._fail_rollback is not None:
            raise self._fail_rollback
        self._wrapped.rollback()

    def close(self) -> None:
        self._counts.close += 1
        if self._fail_close is not None:
            raise self._fail_close
        self._wrapped.close()


def _seed_policies(store: InMemoryStore, *policies: ConfigurationPolicy) -> None:
    InMemoryConfigurationPolicyRepository(store).seed_if_missing(policies)


def _make_service(
    store: InMemoryStore,
    *,
    registry: AdapterRegistry | None = None,
    snapshot_id_factory: Callable[[], str] | None = None,
    policy_evaluator: Callable[..., Any] | None = None,
    incident_candidate_factory: Callable[..., Any] | None = None,
) -> ConfigIngestionService:
    kwargs: dict[str, Any] = {
        "unit_of_work_factory": lambda: InMemoryUnitOfWork(store),
        "adapter_registry": registry or AdapterRegistry([CiscoAdapter()]),
    }
    if snapshot_id_factory is not None:
        kwargs["snapshot_id_factory"] = snapshot_id_factory
    if policy_evaluator is not None:
        kwargs["policy_evaluator"] = policy_evaluator
    if incident_candidate_factory is not None:
        kwargs["incident_candidate_factory"] = incident_candidate_factory
    return ConfigIngestionService(**kwargs)


# --- The first test, per Day 5A plan item 12 ------------------------------


def test_config_ingestion_service__new_device_satisfied_policy__returns_zero_incidents() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a())
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    result = service.ingest(_command())

    assert result.device_id == DEVICE_ID
    assert result.snapshot_id == "snap-1"
    assert result.violations_detected == 0
    assert result.incidents_created == 0
    assert result.incidents_updated == 0


# --- Pre-persistence behavior ----------------------------------------------


def test_pre_persistence__correct_adapter_is_resolved__and_others_untouched() -> None:
    cisco_spy = _SpyAdapter(CiscoAdapter())
    arista_spy = _SpyAdapter(_FakeAristaAdapter())
    store = InMemoryStore()
    service = _make_service(
        store,
        registry=AdapterRegistry([cisco_spy, arista_spy]),
        snapshot_id_factory=lambda: "snap-1",
    )

    service.ingest(_command(vendor="cisco-ios-xe"))

    assert cisco_spy.calls == [_SATISFIED_RAW_CONFIG]
    assert arista_spy.calls == []


def test_pre_persistence__exact_raw_text_is_passed_to_parse() -> None:
    spy = _SpyAdapter(CiscoAdapter())
    store = InMemoryStore()
    service = _make_service(
        store, registry=AdapterRegistry([spy]), snapshot_id_factory=lambda: "snap-1"
    )
    raw_text = _SATISFIED_RAW_CONFIG + " \n"

    service.ingest(_command(raw_config_text=raw_text))

    assert spy.calls == [raw_text]


def test_pre_persistence__adapter_parse_called_exactly_once() -> None:
    spy = _SpyAdapter(CiscoAdapter())
    store = InMemoryStore()
    service = _make_service(
        store, registry=AdapterRegistry([spy]), snapshot_id_factory=lambda: "snap-1"
    )

    service.ingest(_command())

    assert len(spy.calls) == 1


def test_pre_persistence__unsupported_vendor__raises_unsupported_vendor_error() -> None:
    store = InMemoryStore()
    service = _make_service(store)

    with pytest.raises(UnsupportedVendorError):
        service.ingest(_command(vendor="juniper-junos"))


def test_pre_persistence__unsupported_vendor__creates_no_unit_of_work() -> None:
    store = InMemoryStore()
    counting_factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = ConfigIngestionService(
        unit_of_work_factory=counting_factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
    )

    with pytest.raises(UnsupportedVendorError):
        service.ingest(_command(vendor="juniper-junos"))

    assert counting_factory.call_count == 0


def test_pre_persistence__parse_error__raises_configuration_parse_error() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    with pytest.raises(ConfigurationParseError):
        service.ingest(_command(raw_config_text=_NO_HOSTNAME_RAW_CONFIG))


def test_pre_persistence__configuration_parse_error__preserves_the_parse_error_value() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    with pytest.raises(ConfigurationParseError) as exc_info:
        service.ingest(_command(raw_config_text=_NO_HOSTNAME_RAW_CONFIG))

    assert exc_info.value.parse_error.code == ParseErrorCode.MISSING_HOSTNAME


def test_pre_persistence__parse_failure__creates_no_unit_of_work() -> None:
    store = InMemoryStore()
    counting_factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = ConfigIngestionService(
        unit_of_work_factory=counting_factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
    )

    with pytest.raises(ConfigurationParseError):
        service.ingest(_command(raw_config_text=_NO_HOSTNAME_RAW_CONFIG))

    assert counting_factory.call_count == 0


def test_pre_persistence__parse_failure__snapshot_id_factory_never_called() -> None:
    store = InMemoryStore()
    id_factory = _CountingFactory(lambda: "snap-1")
    service = _make_service(store, snapshot_id_factory=id_factory)

    with pytest.raises(ConfigurationParseError):
        service.ingest(_command(raw_config_text=_NO_HOSTNAME_RAW_CONFIG))

    assert id_factory.call_count == 0


def test_pre_persistence__invalid_generated_id__raises_value_error() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "   ")

    with pytest.raises(ValueError, match="snapshot_id"):
        service.ingest(_command())


def test_pre_persistence__invalid_generated_id__creates_no_unit_of_work() -> None:
    store = InMemoryStore()
    counting_factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = ConfigIngestionService(
        unit_of_work_factory=counting_factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "",
    )

    with pytest.raises(ValueError, match="snapshot_id"):
        service.ingest(_command())

    assert counting_factory.call_count == 0


def test_pre_persistence__snapshot_id_factory_called_exactly_once_after_normalization() -> None:
    store = InMemoryStore()
    id_factory = _CountingFactory(lambda: "snap-1")
    service = _make_service(store, snapshot_id_factory=id_factory)

    service.ingest(_command())

    assert id_factory.call_count == 1


# --- Successful persistence -------------------------------------------------


def test_success__new_device_is_created() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    service.ingest(_command())

    device = InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.device_id == DEVICE_ID
    assert device.vendor == VendorType.CISCO_IOS_XE


def test_success__exact_raw_text_and_hash_are_persisted() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    service.ingest(_command())

    snapshot = InMemoryUnitOfWork(store).configuration_snapshots.get_by_id("snap-1")
    assert snapshot is not None
    assert snapshot.raw_config_text == _SATISFIED_RAW_CONFIG
    assert snapshot.raw_text_hash == compute_raw_text_hash(_SATISFIED_RAW_CONFIG)


def test_success__returned_normalized_configuration_is_persisted() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    result = service.ingest(_command())

    snapshot = InMemoryUnitOfWork(store).configuration_snapshots.get_by_id("snap-1")
    assert snapshot is not None
    assert snapshot.normalized_config == result.normalized_config
    assert snapshot.normalized_config.hostname == "spine-01"


def test_success__first_snapshot_becomes_current_and_baseline() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    service.ingest(_command())

    device = InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-1"
    assert device.baseline_snapshot_id == "snap-1"


def test_success__later_snapshot_replaces_current_and_preserves_baseline() -> None:
    store = InMemoryStore()
    ids = iter(["snap-1", "snap-2"])
    service = _make_service(store, snapshot_id_factory=lambda: next(ids))

    service.ingest(_command(observed_at=T0))
    service.ingest(_command(observed_at=T1))

    device = InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-2"
    assert device.baseline_snapshot_id == "snap-1"


def test_success__created_at_preserved_and_updated_at_advances() -> None:
    store = InMemoryStore()
    ids = iter(["snap-1", "snap-2"])
    service = _make_service(store, snapshot_id_factory=lambda: next(ids))

    service.ingest(_command(observed_at=T0))
    service.ingest(_command(observed_at=T1))

    device = InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.created_at == T0
    assert device.updated_at == T1


def test_success__no_applicable_policy__yields_zero_violations_and_incidents() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    result = service.ingest(_command())

    assert result.violations_detected == 0
    assert result.incidents_created == 0
    assert result.incidents_updated == 0
    assert InMemoryUnitOfWork(store).incidents.list_all() == ()


def test_success__missing_acl__yields_exactly_one_violation_and_one_created_incident() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a())
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")

    result = service.ingest(_command(raw_config_text=_MISSING_ACL_RAW_CONFIG))

    assert result.violations_detected == 1
    assert result.incidents_created == 1
    assert result.incidents_updated == 0
    incidents = InMemoryUnitOfWork(store).incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].occurrence_count == 1
    assert incidents[0].source is IncidentSource.POLICY_VIOLATION


def test_success__repeated_missing_acl__updates_the_same_incident() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a())
    ids = iter(["snap-1", "snap-2"])
    service = _make_service(store, snapshot_id_factory=lambda: next(ids))

    service.ingest(_command(raw_config_text=_MISSING_ACL_RAW_CONFIG, observed_at=T0))
    first_incident = InMemoryUnitOfWork(store).incidents.list_all()[0]

    result = service.ingest(_command(raw_config_text=_MISSING_ACL_RAW_CONFIG, observed_at=T1))

    assert result.violations_detected == 1
    assert result.incidents_created == 0
    assert result.incidents_updated == 1
    incidents = InMemoryUnitOfWork(store).incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].incident_id == first_incident.incident_id
    assert incidents[0].occurrence_count == 2


def test_success__multiple_violations_processed_in_deterministic_order() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a(), _policy_b())
    received_order: list[tuple[str, str]] = []

    def recording_factory(violation: Any) -> Any:
        received_order.append((violation.rule_ref, violation.affected_resource))
        return IncidentFactory.build_candidate(violation)

    service = _make_service(
        store,
        snapshot_id_factory=lambda: "snap-1",
        incident_candidate_factory=recording_factory,
    )

    result = service.ingest(_command(raw_config_text=_MULTI_VIOLATION_RAW_CONFIG))

    assert result.violations_detected == 2
    assert result.incidents_created == 2
    assert received_order == [
        ("policy-a-acl-external-in", "interface:GigabitEthernet0/1:acl_in"),
        ("policy-b-acl-mgmt-in", "interface:GigabitEthernet0/2:acl_in"),
    ]


def test_success__generated_snapshot_id_appears_in_snapshot_incident_and_result() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a())
    service = _make_service(store, snapshot_id_factory=lambda: "snap-xyz")

    result = service.ingest(_command(raw_config_text=_MISSING_ACL_RAW_CONFIG))

    assert result.snapshot_id == "snap-xyz"
    snapshot = InMemoryUnitOfWork(store).configuration_snapshots.get_by_id("snap-xyz")
    assert snapshot is not None
    incidents = InMemoryUnitOfWork(store).incidents.list_all()
    assert incidents[0].evidence.source_snapshot_id == "snap-xyz"


def test_success__commit_called_exactly_once_and_close_called_exactly_once() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = ConfigIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
    )

    service.ingest(_command())

    assert counts.commit == 1
    assert counts.rollback == 0
    assert counts.close == 1


# --- Failure and rollback ----------------------------------------------------


def test_failure__vendor_mismatch__raises_device_conflict_error_and_rolls_back() -> None:
    store = InMemoryStore()
    registry = AdapterRegistry([CiscoAdapter(), _FakeAristaAdapter()])
    ids = iter(["snap-1", "snap-2"])
    service = _make_service(store, registry=registry, snapshot_id_factory=lambda: next(ids))
    service.ingest(_command(vendor="cisco-ios-xe", observed_at=T0))

    with pytest.raises(DeviceConflictError):
        service.ingest(_command(vendor="arista-eos", observed_at=T1))

    verify_uow = InMemoryUnitOfWork(store)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.vendor == VendorType.CISCO_IOS_XE
    assert device.current_snapshot_id == "snap-1"
    assert verify_uow.configuration_snapshots.get_by_id("snap-2") is None


def test_failure__stale_device_timestamp__raises_device_conflict_error_and_rolls_back() -> None:
    # Device.__post_init__ itself already rejects updated_at < created_at, so
    # proving the *repository-level* "updated_at may not move backward"
    # check (as opposed to that domain-level invariant) needs a device
    # that's already been updated once, then a retry strictly between
    # created_at and that later updated_at.
    store = InMemoryStore()
    ids = iter(["snap-1", "snap-2", "snap-3"])
    service = _make_service(store, snapshot_id_factory=lambda: next(ids))
    service.ingest(_command(observed_at=T0))
    service.ingest(_command(observed_at=T1))
    mid_range_stale = T0 + (T1 - T0) / 2

    with pytest.raises(DeviceConflictError):
        service.ingest(_command(observed_at=mid_range_stale))

    verify_uow = InMemoryUnitOfWork(store)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.updated_at == T1
    assert device.current_snapshot_id == "snap-2"
    assert verify_uow.configuration_snapshots.get_by_id("snap-3") is None


def test_failure__duplicate_snapshot_id__raises_and_rolls_back() -> None:
    store = InMemoryStore()
    service = _make_service(store, snapshot_id_factory=lambda: "snap-1")
    service.ingest(_command(observed_at=T0))

    with pytest.raises(SnapshotAlreadyExistsError):
        service.ingest(_command(observed_at=T1))

    verify_uow = InMemoryUnitOfWork(store)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.updated_at == T0


def test_failure__policy_evaluator_failure__rolls_back_everything() -> None:
    store = InMemoryStore()

    def failing_evaluator(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("evaluator boom")

    service = _make_service(
        store, snapshot_id_factory=lambda: "snap-1", policy_evaluator=failing_evaluator
    )

    with pytest.raises(RuntimeError, match="evaluator boom"):
        service.ingest(_command())

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.devices.get_by_id(DEVICE_ID) is None
    assert verify_uow.configuration_snapshots.get_by_id("snap-1") is None


def test_failure__incident_factory_failure__rolls_back_everything() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a())

    def failing_factory(violation: Any) -> Any:
        raise RuntimeError("incident factory boom")

    service = _make_service(
        store,
        snapshot_id_factory=lambda: "snap-1",
        incident_candidate_factory=failing_factory,
    )

    with pytest.raises(RuntimeError, match="incident factory boom"):
        service.ingest(_command(raw_config_text=_MISSING_ACL_RAW_CONFIG))

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.devices.get_by_id(DEVICE_ID) is None
    assert verify_uow.configuration_snapshots.get_by_id("snap-1") is None
    assert verify_uow.incidents.list_all() == ()


def test_failure__later_violation_failure__rolls_back_earlier_incident_mutation_too() -> None:
    store = InMemoryStore()
    _seed_policies(store, _policy_a(), _policy_b())
    call_count = 0

    def flaky_factory(violation: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("boom on second violation")
        return IncidentFactory.build_candidate(violation)

    service = _make_service(
        store,
        snapshot_id_factory=lambda: "snap-1",
        incident_candidate_factory=flaky_factory,
    )

    with pytest.raises(RuntimeError, match="boom on second violation"):
        service.ingest(_command(raw_config_text=_MULTI_VIOLATION_RAW_CONFIG))

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.devices.get_by_id(DEVICE_ID) is None
    assert verify_uow.configuration_snapshots.get_by_id("snap-1") is None
    assert verify_uow.incidents.list_all() == ()


def test_failure__commit_failure__preserves_the_original_exception() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    commit_error = RuntimeError("commit boom")
    service = ConfigIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_commit=commit_error
        ),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
    )

    with pytest.raises(RuntimeError, match="commit boom") as exc_info:
        service.ingest(_command())

    assert exc_info.value is commit_error
    assert counts.commit == 1
    assert counts.rollback == 1
    assert counts.close == 1


def test_failure__rollback_also_fails__original_processing_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    rollback_error = RuntimeError("rollback boom")

    def failing_evaluator(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("processing boom")

    service = ConfigIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_rollback=rollback_error
        ),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
        policy_evaluator=failing_evaluator,
    )

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.ingest(_command())

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert counts.rollback == 1
    assert counts.close == 1


def test_failure__close_also_fails__original_processing_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    close_error = RuntimeError("close boom")

    def failing_evaluator(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("processing boom")

    service = ConfigIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_close=close_error
        ),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
        policy_evaluator=failing_evaluator,
    )

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.ingest(_command())

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
    assert counts.close == 1


def test_failure__close_is_attempted_exactly_once_after_failure() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()

    def failing_evaluator(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    service = ConfigIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: "snap-1",
        policy_evaluator=failing_evaluator,
    )

    with pytest.raises(RuntimeError, match="boom"):
        service.ingest(_command())

    assert counts.close == 1
    assert counts.commit == 0
    assert counts.rollback == 1
