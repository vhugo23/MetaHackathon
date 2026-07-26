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
from dataclasses import fields as dataclasses_fields
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.models import TelemetryIngestionCommand, TelemetryIngestionResult
from meta_rne.application.telemetry_ingestion import TelemetryIngestionService
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.domain.anomaly import RuleId
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.incident import IncidentSource, compute_fingerprint
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


# --- Gate H3: anomaly-to-incident integration through real PostgreSQL -------
#
# AnomalyIncidentMapper and compute_fingerprint are used exactly as approved
# (Gate H2) — no severity/recommendation/affected_resource logic is
# duplicated here.


def _incidents(session_factory: Callable[[], Session]) -> tuple[object, ...]:
    uow = SqlAlchemyUnitOfWork(session_factory)
    result = uow.incidents.list_all()
    uow.close()
    return result


class _FailingIncidentsWrapper:
    """Wraps a real SqlAlchemyIncidentRepository, raising on a specific
    1-indexed upsert_open_incident call — used only to prove atomicity,
    never to replace the real repository's dedup/persistence behavior."""

    def __init__(self, wrapped: object, fail_on_call: int, fail_error: Exception) -> None:
        self._wrapped = wrapped
        self._fail_on_call = fail_on_call
        self._fail_error = fail_error
        self.calls: list[object] = []

    def upsert_open_incident(
        self, candidate: object, fingerprint: str, observed_at: object
    ) -> object:
        self.calls.append(candidate)
        if len(self.calls) == self._fail_on_call:
            raise self._fail_error
        return self._wrapped.upsert_open_incident(  # type: ignore[attr-defined]
            candidate, fingerprint, observed_at
        )

    def get_by_id(self, incident_id: str) -> object:
        return self._wrapped.get_by_id(incident_id)  # type: ignore[attr-defined]

    def list_all(self) -> object:
        return self._wrapped.list_all()  # type: ignore[attr-defined]

    def resolve(self, incident_id: str, resolved_at: object) -> object:
        return self._wrapped.resolve(incident_id, resolved_at)  # type: ignore[attr-defined]


def test_telemetry_ingestion_postgres__cpu_anomaly__persists_one_anomaly_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0, cpu=95.0))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    incidents = _incidents(sqlalchemy_session_factory)
    assert len(incidents) == 1
    assert incidents[0].source == IncidentSource.ANOMALY
    assert incidents[0].rule_ref == "RULE-CPU-HIGH"
    assert len(result.anomalies) == 1


def test_telemetry_ingestion_postgres__link_flap_anomaly__persists_one_anomaly_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            sqlalchemy_session_factory,
            _sample(
                sampled_at=T0 + timedelta(seconds=12 * offset),
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    service = _service(sqlalchemy_session_factory)

    service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=48),
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
            )
        )
    )

    incidents = _incidents(sqlalchemy_session_factory)
    assert len(incidents) == 1
    assert incidents[0].source == IncidentSource.ANOMALY
    assert incidents[0].rule_ref == "RULE-LINK-FLAP"


def test_telemetry_ingestion_postgres__bgp_down_anomaly__persists_one_anomaly_incident(
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

    service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=30),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    incidents = _incidents(sqlalchemy_session_factory)
    assert len(incidents) == 1
    assert incidents[0].source == IncidentSource.ANOMALY
    assert incidents[0].rule_ref == "RULE-BGP-DOWN"


def test_telemetry_ingestion_postgres__anomaly_incident_fields_match_mapper_exactly(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0, cpu=95.0))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    anomaly = result.anomalies[0]
    expected_candidate = AnomalyIncidentMapper.build_candidate(anomaly)
    expected_fingerprint = compute_fingerprint(
        expected_candidate.device_id,
        expected_candidate.source,
        expected_candidate.rule_ref,
        expected_candidate.affected_resource,
    )

    incidents = _incidents(sqlalchemy_session_factory)
    incident = incidents[0]
    assert incident.device_id == expected_candidate.device_id
    assert incident.source == expected_candidate.source
    assert incident.rule_ref == expected_candidate.rule_ref
    assert incident.affected_resource == expected_candidate.affected_resource
    assert incident.severity == expected_candidate.severity
    assert incident.recommendation == expected_candidate.recommendation
    assert incident.fingerprint == expected_fingerprint


def test_telemetry_ingestion_postgres__repeated_cpu_anomaly__updates_one_open_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)

    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0, cpu=95.0))
    first_result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    second_result = service.ingest(
        _command(
            sample=_sample(sampled_at=T0 + timedelta(seconds=60), cpu=95.0),
            observed_at=T0 + timedelta(seconds=60),
        )
    )

    incidents = _incidents(sqlalchemy_session_factory)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.occurrence_count == 2
    assert incident.created_at == T0
    assert incident.last_seen_at == T0 + timedelta(seconds=60)
    assert incident.evidence == second_result.anomalies[0].evidence
    assert incident.evidence != first_result.anomalies[0].evidence


def test_telemetry_ingestion_postgres__multiple_anomalies__persists_all_in_one_transaction(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            sqlalchemy_session_factory,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        sqlalchemy_session_factory,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service = _service(sqlalchemy_session_factory)

    service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=50),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    incidents = _incidents(sqlalchemy_session_factory)
    assert len(incidents) == 3
    assert {i.rule_ref for i in incidents} == {"RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"}


def test_telemetry_ingestion_postgres__failure_during_incident_upsert__rolls_back_everything(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            sqlalchemy_session_factory,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        sqlalchemy_session_factory,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )

    def factory() -> SqlAlchemyUnitOfWork:
        uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
        uow.incidents = _FailingIncidentsWrapper(  # type: ignore[assignment]
            uow.incidents, fail_on_call=2, fail_error=RuntimeError("second upsert boom")
        )
        return uow

    service = TelemetryIngestionService(unit_of_work_factory=factory)

    with pytest.raises(RuntimeError, match="second upsert boom"):
        service.ingest(
            _command(
                sample=_sample(
                    sampled_at=T0 + timedelta(seconds=50),
                    cpu=95.0,
                    interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                    bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
                )
            )
        )

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    # The five seeded history samples remain (they were committed before
    # this test's own failing ingestion began) — only the current
    # ingestion's own sample (sampled_at=T0+50s) must be absent, proving
    # its save was rolled back along with the incident upserts.
    latest = verify_uow.telemetry_samples.get_latest(DEVICE_ID)
    assert latest is not None
    assert latest.sampled_at != T0 + timedelta(seconds=50)
    assert verify_uow.incidents.list_all() == ()
    verify_uow.close()


def test_telemetry_ingestion_postgres__no_anomaly__existing_telemetry_only_scenario_unchanged(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    sample = _sample()
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(_command(sample=sample))

    assert result.sample == sample
    assert result.anomalies == ()
    assert _incidents(sqlalchemy_session_factory) == ()


def test_telemetry_ingestion_postgres__result_remains_sample_and_anomalies_only(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0, cpu=95.0))
    service = _service(sqlalchemy_session_factory)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    assert {f.name for f in dataclasses_fields(TelemetryIngestionResult)} == {
        "sample",
        "anomalies",
    }
    assert result.sample.cpu_utilization_pct == 95.0
