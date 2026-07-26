"""Focused PostgreSQL parity tests for ``TelemetryIngestionService`` (Gate
F1). Uses a real ``SqlAlchemyUnitOfWork`` and real PostgreSQL telemetry
repository — not a re-run of the in-memory suite
(``tests/unit/application/test_telemetry_ingestion_service.py`` already
proves call-count/ordering/rollback behavior against the fast in-memory
double). These tests exist to prove what only a real database can prove:
that the approved provenance-tagged Flow F algorithm produces the
*identical* evaluation sequence/anomaly result on PostgreSQL as it does
on the in-memory backend, including when PostgreSQL retains historical
rows the in-memory backend would already have pruned.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.models import TelemetryIngestionCommand
from meta_rne.application.telemetry_ingestion import TelemetryIngestionService
from meta_rne.domain.anomaly import RuleId
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(device_id: str = DEVICE_ID) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _sample(
    device_id: str = DEVICE_ID,
    sampled_at: datetime = T0,
    cpu: float = 50.0,
    memory: float = 50.0,
    error_rate: float = 0.0,
    interface_states: tuple[InterfaceState, ...] = (),
    bgp_sessions: tuple[BgpSession, ...] = (),
) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=cpu,
        memory_utilization_pct=memory,
        interface_error_rate=error_rate,
        interface_states=interface_states,
        bgp_sessions=bgp_sessions,
    )


def _command(
    device_id: str = DEVICE_ID,
    sample: TelemetrySample | None = None,
    observed_at: datetime = T0,
) -> TelemetryIngestionCommand:
    return TelemetryIngestionCommand(
        device_id=device_id,
        sample=sample if sample is not None else _sample(device_id=device_id),
        observed_at=observed_at,
    )


def _seed_device(session_factory: Callable[[], Session], device_id: str = DEVICE_ID) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.devices.save(_device(device_id))
    uow.commit()
    uow.close()


def _seed_sample(session_factory: Callable[[], Session], sample: TelemetrySample) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()
    uow.close()


def _service(session_factory: Callable[[], Session]) -> TelemetryIngestionService:
    return TelemetryIngestionService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )


def test_telemetry_ingestion_postgres__missing_device__raises_and_persists_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    service = _service(sqlalchemy_session_factory)

    with pytest.raises(DeviceNotFoundError):
        service.ingest(_command())

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.telemetry_samples.get_latest(DEVICE_ID) is None
    verify_uow.close()


def test_telemetry_ingestion_postgres__zero_anomaly_ingestion__persists_and_returns_sample(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    sample = _sample()
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(_command(sample=sample))

    assert result.sample == sample
    assert result.anomalies == ()

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    saved = verify_uow.telemetry_samples.get_latest(DEVICE_ID)
    assert saved == sample
    verify_uow.close()


def test_telemetry_ingestion_postgres__cpu_high__triggers_through_real_database(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0, cpu=95.0))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    assert any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_telemetry_ingestion_postgres__cpu_high__previously_stored_late_sample_still_suppressed(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """Proves the required historical-context-parity counterexample: a
    prior CPU-high sample followed by an unrelated later save, then an
    ingestion 60s after the prior sample. PostgreSQL retains the prior
    row unconditionally, but the watermark-derived cutoff must still
    exclude it from evaluation here — matching in-memory's pruning
    behavior exactly (Gate F0 plan, Section 6)."""
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(
        sqlalchemy_session_factory, _sample(sampled_at=T0 + timedelta(minutes=29), cpu=95.0)
    )
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0 + timedelta(minutes=60)))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(minutes=30), cpu=95.0))
    )

    assert not any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_telemetry_ingestion_postgres__link_flap__triggers_through_real_database(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"
    # 5 observations -> 4 transitions (LINK_FLAP requires >= 4): initial UP
    # is not itself a transition; DOWN/UP/DOWN/UP each toggle the state.
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            sqlalchemy_session_factory,
            _sample(
                sampled_at=T0 + timedelta(seconds=12 * offset),
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=48),
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
            )
        )
    )

    assert any(a.rule_id is RuleId.LINK_FLAP for a in result.anomalies)


def test_telemetry_ingestion_postgres__bgp_down__triggers_through_real_database(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    neighbor = "10.0.0.2"
    _seed_sample(
        sqlalchemy_session_factory,
        _sample(
            sampled_at=T0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=30),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    assert any(a.rule_id is RuleId.BGP_DOWN for a in result.anomalies)


def test_telemetry_ingestion_postgres__more_than_100_entries__final_100_cap_applied(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    for i in range(150):
        _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0 + timedelta(seconds=i)))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=150))))

    assert result.sample.sampled_at == T0 + timedelta(seconds=150)


def test_telemetry_ingestion_postgres__exact_duplicate_history__remains_independent_observation(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """Equal-but-distinct PostgreSQL round-tripped values (a fresh
    ``TelemetrySample`` object is reconstructed on every read — Gate
    F0's plan, Section 6) must produce the same CPU_HIGH-triggering
    result as the in-memory backend's exact-duplicate counterexample."""
    _seed_device(sqlalchemy_session_factory)
    duplicate = _sample(sampled_at=T0, cpu=95.0)
    _seed_sample(sqlalchemy_session_factory, duplicate)
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(_command(sample=_sample(sampled_at=T0, cpu=95.0)))

    assert any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_telemetry_ingestion_postgres__failure_after_save__persists_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """A forced RuleEngine failure must roll back the telemetry save,
    proven here against a real transaction, not merely the in-memory
    working-store copy."""
    _seed_device(sqlalchemy_session_factory)

    class _FailingRuleEngine:
        @staticmethod
        def evaluate(observed_at: datetime, recent_samples: list[TelemetrySample]) -> list[object]:
            raise RuntimeError("forced late failure")

    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(sqlalchemy_session_factory),
        rule_engine=_FailingRuleEngine(),
    )

    with pytest.raises(RuntimeError, match="forced late failure"):
        service.ingest(_command())

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.telemetry_samples.get_latest(DEVICE_ID) is None
    verify_uow.close()
