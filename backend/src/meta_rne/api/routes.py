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
    IncidentResponse,
    SubmitConfigurationRequest,
    SubmitConfigurationResponse,
)
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.incident_queries import ListIncidentsService
from meta_rne.application.models import IngestConfigurationCommand


def build_router(
    *,
    config_ingestion_service: ConfigIngestionService,
    list_incidents_service: ListIncidentsService,
    clock: Callable[[], datetime],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/devices/{device_id}/config",
        status_code=status.HTTP_201_CREATED,
        response_model=SubmitConfigurationResponse,
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

    @router.get("/incidents", response_model=list[IncidentResponse])
    def list_incidents() -> list[IncidentResponse]:
        incidents = list_incidents_service.list_all()
        return [IncidentResponse.from_domain(incident) for incident in incidents]

    return router
