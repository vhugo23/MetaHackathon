"""``ConfigIngestionService`` — Day 5A's one application-level workflow.

Orchestrates the existing domain/detection/persistence components across
exactly one ``UnitOfWork`` per successful ``ingest()`` call. The
pre-transaction boundary (Day 5A plan item 4) is binding: command
validation, adapter resolution, parsing, snapshot-ID generation/validation,
and canonical-vendor derivation all happen *before* any ``UnitOfWork`` is
constructed, so an unsupported vendor, a parse failure, or an invalid
generated snapshot ID creates zero UnitOfWorks. Only once a
``ConfigurationSnapshot`` is fully constructed does the service create one
``UnitOfWork`` and use it for every persistence operation in the call.

Does not seed policies, does not deduplicate incidents outside
``IncidentRepository``, and does not construct ``Incident`` domain objects
directly — ``IncidentRepository.upsert_open_incident`` remains the only
write path (domain-model.md Section 11).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.application.errors import ConfigurationParseError
from meta_rne.application.models import ConfigIngestionResult, IngestConfigurationCommand
from meta_rne.application.snapshot_id import default_snapshot_id_factory
from meta_rne.detection.incident_factory import IncidentFactory
from meta_rne.detection.policy_evaluator import PolicyEvaluator
from meta_rne.domain.config import NormalizedConfiguration, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.errors import ParseError
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentUpsertOutcome,
    compute_fingerprint,
)
from meta_rne.domain.policy import ConfigurationViolation
from meta_rne.domain.ports import UnitOfWork
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash


@dataclass(frozen=True, slots=True)
class _PreparedIngestion:
    """Everything the pre-transaction boundary produces — never partial;
    either this is fully built, or ``ingest()`` has already raised and no
    ``UnitOfWork`` was ever created."""

    device_id: str
    canonical_vendor: VendorType
    snapshot: ConfigurationSnapshot


class ConfigIngestionService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        adapter_registry: AdapterRegistry,
        snapshot_id_factory: Callable[[], str] = default_snapshot_id_factory,
        policy_evaluator: Callable[
            ..., tuple[ConfigurationViolation, ...]
        ] = PolicyEvaluator.evaluate,
        incident_candidate_factory: Callable[
            [ConfigurationViolation], IncidentCandidate
        ] = IncidentFactory.build_candidate,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_registry = adapter_registry
        self._snapshot_id_factory = snapshot_id_factory
        self._policy_evaluator = policy_evaluator
        self._incident_candidate_factory = incident_candidate_factory

    def _prepare(self, command: IngestConfigurationCommand) -> _PreparedIngestion:
        adapter = self._adapter_registry.resolve(command.vendor)

        parsed: NormalizedConfiguration | ParseError = adapter.parse(command.raw_config_text)
        if isinstance(parsed, ParseError):
            raise ConfigurationParseError(parsed)

        canonical_vendor = VendorType(adapter.vendor_id)

        snapshot_id = self._snapshot_id_factory()
        if not snapshot_id.strip():
            raise ValueError("generated snapshot_id must not be empty or whitespace-only")

        snapshot = ConfigurationSnapshot(
            snapshot_id=snapshot_id,
            device_id=command.device_id,
            vendor=canonical_vendor,
            raw_config_text=command.raw_config_text,
            raw_text_hash=compute_raw_text_hash(command.raw_config_text),
            normalized_config=parsed,
            submitted_at=command.observed_at,
        )

        return _PreparedIngestion(
            device_id=command.device_id,
            canonical_vendor=canonical_vendor,
            snapshot=snapshot,
        )

    def _persist_device_and_snapshot(
        self, uow: UnitOfWork, prepared: _PreparedIngestion, observed_at: datetime
    ) -> None:
        existing_device = uow.devices.get_by_id(prepared.device_id)

        if existing_device is None:
            uow.devices.save(
                Device(
                    device_id=prepared.device_id,
                    vendor=prepared.canonical_vendor,
                    current_snapshot_id=None,
                    baseline_snapshot_id=None,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
            uow.configuration_snapshots.add(prepared.snapshot)
            uow.devices.save(
                Device(
                    device_id=prepared.device_id,
                    vendor=prepared.canonical_vendor,
                    current_snapshot_id=prepared.snapshot.snapshot_id,
                    baseline_snapshot_id=prepared.snapshot.snapshot_id,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
        else:
            uow.configuration_snapshots.add(prepared.snapshot)
            uow.devices.save(
                Device(
                    device_id=existing_device.device_id,
                    vendor=prepared.canonical_vendor,
                    current_snapshot_id=prepared.snapshot.snapshot_id,
                    baseline_snapshot_id=existing_device.baseline_snapshot_id,
                    created_at=existing_device.created_at,
                    updated_at=observed_at,
                )
            )

    def _evaluate_and_upsert_incidents(
        self, uow: UnitOfWork, prepared: _PreparedIngestion, observed_at: datetime
    ) -> tuple[int, int, int]:
        policies = uow.configuration_policies.get_applicable_to_device(prepared.device_id)
        violations = self._policy_evaluator(
            prepared.device_id,
            prepared.snapshot.snapshot_id,
            observed_at,
            prepared.snapshot.normalized_config,
            policies,
        )

        created = 0
        updated = 0
        for violation in violations:
            candidate = self._incident_candidate_factory(violation)
            fingerprint = compute_fingerprint(
                candidate.device_id,
                candidate.source,
                candidate.rule_ref,
                candidate.affected_resource,
            )
            result = uow.incidents.upsert_open_incident(candidate, fingerprint, observed_at)
            if result.outcome is IncidentUpsertOutcome.CREATED:
                created += 1
            else:
                updated += 1

        return len(violations), created, updated

    def ingest(self, command: IngestConfigurationCommand) -> ConfigIngestionResult:
        prepared = self._prepare(command)

        uow = self._unit_of_work_factory()
        try:
            self._persist_device_and_snapshot(uow, prepared, command.observed_at)
            violations_detected, created, updated = self._evaluate_and_upsert_incidents(
                uow, prepared, command.observed_at
            )
            uow.commit()
        except Exception as original_error:
            try:
                uow.rollback()
            except Exception as rollback_error:
                original_error.add_note(f"UnitOfWork rollback also failed: {rollback_error!r}")

            try:
                uow.close()
            except Exception as close_error:
                original_error.add_note(f"UnitOfWork close also failed: {close_error!r}")

            raise
        else:
            uow.close()
            return ConfigIngestionResult(
                device_id=prepared.device_id,
                snapshot_id=prepared.snapshot.snapshot_id,
                normalized_config=prepared.snapshot.normalized_config,
                violations_detected=violations_detected,
                incidents_created=created,
                incidents_updated=updated,
            )
