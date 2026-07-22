"""FastAPI application entry point (Day 5B).

``create_app`` is a controlled composition factory — importing this module
never creates a SQLAlchemy engine or ``Session`` and never requires
``DATABASE_URL`` to be set: the module-level ``app`` below only registers
routes, CORS middleware (if any origins are configured), and a lifespan
callback — it does not invoke the lifespan or open a connection.
Production engine construction is lazy (``api/dependencies.py``'s
``_LazySqlAlchemyUnitOfWorkFactory``), happening on first actual request or
lifespan startup, whichever comes first — never at import time. Every
request gets its own ``UnitOfWork``/``Session`` because
``ConfigIngestionService``/``ListIncidentsService`` each call the injected
``unit_of_work_factory`` fresh, once per operation (Day 5A/5B).

Tests never mutate this module-level ``app`` or use
``app.dependency_overrides`` — each test builds its own fully-isolated
``create_app(...)`` instance directly (Day 5B binding correction).
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.clock import CallableClock, utc_now
from meta_rne.api.cors import cors_allowed_origins_from_environment
from meta_rne.api.dependencies import (
    build_lazy_sqlalchemy_unit_of_work_factory,
    build_production_adapter_registry,
    seed_slice1_policies,
)
from meta_rne.api.errors import register_exception_handlers
from meta_rne.api.routes import build_router
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.device_drift import GetDeviceDriftService
from meta_rne.application.incident_queries import ListIncidentsService
from meta_rne.application.incident_resolution import ResolveIncidentService
from meta_rne.application.snapshot_id import default_snapshot_id_factory
from meta_rne.domain.ports import UnitOfWork


def create_app(
    *,
    database_url: str | None = None,
    clock: Callable[[], datetime] = utc_now,
    snapshot_id_factory: Callable[[], str] = default_snapshot_id_factory,
    unit_of_work_factory: Callable[[], UnitOfWork] | None = None,
    adapter_registry: AdapterRegistry | None = None,
    seed_on_startup: bool = True,
    cors_allowed_origins: tuple[str, ...] = (),
) -> FastAPI:
    registry = adapter_registry or build_production_adapter_registry()

    lazy_factory = None
    if unit_of_work_factory is None:
        lazy_factory = build_lazy_sqlalchemy_unit_of_work_factory(database_url)
        uow_factory: Callable[[], UnitOfWork] = lazy_factory
    else:
        uow_factory = unit_of_work_factory

    config_ingestion_service = ConfigIngestionService(
        unit_of_work_factory=uow_factory,
        adapter_registry=registry,
        snapshot_id_factory=snapshot_id_factory,
    )
    list_incidents_service = ListIncidentsService(unit_of_work_factory=uow_factory)
    resolve_incident_service = ResolveIncidentService(
        unit_of_work_factory=uow_factory, clock=CallableClock(clock)
    )
    get_device_drift_service = GetDeviceDriftService(unit_of_work_factory=uow_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if seed_on_startup:
            seed_slice1_policies(uow_factory, clock)
        yield
        if lazy_factory is not None and lazy_factory.engine is not None:
            lazy_factory.engine.dispose()

    app = FastAPI(title="Meta RNE Platform", lifespan=lifespan)

    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    @app.get("/health", operation_id="health_check")
    def get_health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(
        build_router(
            config_ingestion_service=config_ingestion_service,
            list_incidents_service=list_incidents_service,
            resolve_incident_service=resolve_incident_service,
            get_device_drift_service=get_device_drift_service,
            clock=clock,
        )
    )

    register_exception_handlers(app)

    return app


# Production wiring: cors_allowed_origins's own Python-level default is an
# empty tuple (non-permissive) — reading META_RNE_CORS_ALLOWED_ORIGINS is an
# explicit choice made here, at the one real Uvicorn entrypoint, not a
# hidden env-var fallback inside create_app itself.
app = create_app(cors_allowed_origins=cors_allowed_origins_from_environment())
