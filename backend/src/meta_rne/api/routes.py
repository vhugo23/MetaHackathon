"""Route definitions (Day 5B).

``build_router`` closes over the already-constructed application services
and clock — no FastAPI ``Depends``/``dependency_overrides`` indirection.
Each service already creates a fresh ``UnitOfWork``/``Session`` per call
(``ConfigIngestionService.ingest``, ``ListIncidentsService.list_all``), so
a route closing directly over one long-lived service instance still gives
every request its own transaction.
"""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, status

from meta_rne.api.clock import require_utc
from meta_rne.api.schemas import (
    ApiErrorResponse,
    DriftReportResponse,
    IncidentResponse,
    SubmitConfigurationRequest,
    SubmitConfigurationResponse,
    SubmitTelemetryRequest,
    SubmitTelemetryResponse,
    TelemetrySampleResponse,
)
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.device_drift import GetDeviceDriftService
from meta_rne.application.incident_queries import ListIncidentsService
from meta_rne.application.incident_resolution import ResolveIncidentService
from meta_rne.application.models import IngestConfigurationCommand, TelemetryIngestionCommand
from meta_rne.application.telemetry_ingestion import TelemetryIngestionService
from meta_rne.application.telemetry_query import GetRecentTelemetryService
from meta_rne.domain.telemetry import BgpSession, InterfaceState, TelemetrySample

# Documents the real runtime 422 contract (api/errors.py): FastAPI's own
# RequestValidationError (HTTPValidationError, malformed request schema) and
# an application-originated 422 (ApiErrorResponse — unsupported_vendor /
# configuration_parse_error / invalid_request) are both genuine response
# bodies for this status code, never only one of them — see
# docs/frontend-api-contract.md for which case produces which body.
_SUBMIT_CONFIGURATION_422_RESPONSE = {
    "description": (
        "Request-schema validation failure (HTTPValidationError) or an "
        "application-originated error (ApiErrorResponse: unsupported_vendor, "
        "configuration_parse_error, or invalid_request)."
    ),
    "content": {
        "application/json": {
            "schema": {
                "oneOf": [
                    {"$ref": "#/components/schemas/HTTPValidationError"},
                    {"$ref": "#/components/schemas/ApiErrorResponse"},
                ]
            }
        }
    },
}


def build_router(
    *,
    config_ingestion_service: ConfigIngestionService,
    list_incidents_service: ListIncidentsService,
    resolve_incident_service: ResolveIncidentService,
    get_device_drift_service: GetDeviceDriftService,
    telemetry_ingestion_service: TelemetryIngestionService,
    telemetry_query_service: GetRecentTelemetryService,
    clock: Callable[[], datetime],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/devices/{device_id}/config",
        status_code=status.HTTP_201_CREATED,
        response_model=SubmitConfigurationResponse,
        operation_id="submit_device_configuration",
        responses={
            409: {
                "model": ApiErrorResponse,
                "description": (
                    "Persistence conflict: device_conflict, snapshot_already_exists, "
                    "or referenced_device_not_found."
                ),
            },
            422: _SUBMIT_CONFIGURATION_422_RESPONSE,
            500: {
                "model": ApiErrorResponse,
                "description": "persistence_error or serialization_error (generic public detail).",
            },
        },
    )
    def submit_configuration(
        device_id: str, request: SubmitConfigurationRequest
    ) -> SubmitConfigurationResponse:
        observed_at = require_utc(clock())
        command = IngestConfigurationCommand(
            device_id=device_id,
            vendor=request.vendor,
            raw_config_text=request.raw_config_text,
            observed_at=observed_at,
        )
        result = config_ingestion_service.ingest(command)
        return SubmitConfigurationResponse.from_domain(result)

    @router.get(
        "/incidents",
        response_model=list[IncidentResponse],
        operation_id="list_incidents",
        responses={
            500: {
                "model": ApiErrorResponse,
                "description": "persistence_error (generic public detail).",
            },
        },
    )
    def list_incidents() -> list[IncidentResponse]:
        incidents = list_incidents_service.list_all()
        return [IncidentResponse.from_domain(incident) for incident in incidents]

    @router.post(
        "/incidents/{incident_id}/resolve",
        response_model=IncidentResponse,
        operation_id="resolve_incident",
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "incident_not_found.",
            },
        },
    )
    def resolve_incident(incident_id: str) -> IncidentResponse:
        incident = resolve_incident_service.resolve(incident_id)
        return IncidentResponse.from_domain(incident)

    @router.get(
        "/devices/{device_id}/drift",
        response_model=DriftReportResponse,
        operation_id="get_device_drift",
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "device_not_found.",
            },
            500: {
                "description": (
                    "An unmapped internal invariant failure (e.g. a device "
                    "referencing a snapshot that does not exist) returns the "
                    "framework's generic 500 response — plain text "
                    '"Internal Server Error", not this API\'s ApiErrorResponse '
                    "JSON schema. No traceback or other internal detail is "
                    "leaked."
                ),
            },
        },
    )
    def get_device_drift(device_id: str) -> DriftReportResponse:
        report = get_device_drift_service.get_drift(device_id)
        return DriftReportResponse.from_domain(report)

    @router.post(
        "/devices/{device_id}/telemetry",
        status_code=status.HTTP_201_CREATED,
        response_model=SubmitTelemetryResponse,
        operation_id="submit_device_telemetry",
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "device_not_found.",
            },
            422: {
                "description": (
                    "Request-schema validation failure (HTTPValidationError) or an "
                    "application-originated invalid_request ApiErrorResponse."
                ),
            },
        },
    )
    def submit_telemetry(
        device_id: str, request: SubmitTelemetryRequest
    ) -> SubmitTelemetryResponse:
        observed_at = require_utc(clock())

        sample = TelemetrySample(
            device_id=device_id,
            sampled_at=request.sampled_at,
            cpu_utilization_pct=request.cpu_utilization_pct,
            memory_utilization_pct=request.memory_utilization_pct,
            interface_error_rate=request.interface_error_rate,
            interface_states=tuple(
                InterfaceState(name=value.name, oper_state=value.oper_state)
                for value in request.interface_states
            ),
            bgp_sessions=tuple(
                BgpSession(neighbor_ip=value.neighbor_ip, state=value.state)
                for value in request.bgp_sessions
            ),
        )

        result = telemetry_ingestion_service.ingest(
            TelemetryIngestionCommand(
                device_id=device_id,
                sample=sample,
                observed_at=observed_at,
            )
        )
        return SubmitTelemetryResponse.from_domain(result)

    @router.get(
        "/devices/{device_id}/telemetry/recent",
        response_model=list[TelemetrySampleResponse],
        status_code=status.HTTP_200_OK,
        operation_id="get_recent_telemetry",
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "device_not_found.",
            },
            422: {
                "description": (
                    "Request-schema validation failure (HTTPValidationError) or an "
                    "application-originated invalid_request ApiErrorResponse (naive "
                    "or non-UTC-offset since)."
                ),
            },
        },
    )
    def get_recent_telemetry(device_id: str, since: datetime) -> list[TelemetrySampleResponse]:
        samples = telemetry_query_service.get(device_id, since)
        return [TelemetrySampleResponse.from_domain(sample) for sample in samples]

    return router
