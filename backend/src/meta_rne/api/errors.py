"""HTTP error mapping (Day 5B, binding corrections).

Registers one exception handler per named category, most-specific
persistence-conflict subclasses before the generic ``PersistenceError``
base (Starlette resolves by walking the raised exception's MRO for the
most specific registered handler regardless of registration order, but
handlers are still declared in that order here for readability). Pydantic/
FastAPI's own ``RequestValidationError`` 422 response is left untouched —
no custom envelope. Unmapped exceptions and ``InvalidClockError`` are
deliberately never given a handler here, so they fall through to FastAPI's
normal unmapped-exception behavior (500, no leaked detail) rather than a
broad catch-all that would echo exception internals.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from meta_rne.api.schemas import ApiErrorResponse
from meta_rne.application.errors import (
    ConfigurationParseError,
    DeviceNotFoundError,
    IncidentNotFoundError,
)
from meta_rne.domain.errors import UnsupportedVendorError
from meta_rne.persistence.errors import (
    DeviceConflictError,
    PersistenceError,
    ReferencedDeviceNotFoundError,
    SnapshotAlreadyExistsError,
)
from meta_rne.persistence.serialization import SerializationError

_GENERIC_PERSISTENCE_DETAIL = "An internal persistence error occurred."
_GENERIC_SERIALIZATION_DETAIL = "An internal data error occurred."


def _error_response(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiErrorResponse(code=code, detail=detail).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnsupportedVendorError)
    async def _unsupported_vendor(request: Request, exc: UnsupportedVendorError) -> JSONResponse:
        return _error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported_vendor", str(exc))

    @app.exception_handler(ConfigurationParseError)
    async def _configuration_parse_error(
        request: Request, exc: ConfigurationParseError
    ) -> JSONResponse:
        detail = exc.parse_error.message
        if exc.parse_error.line_number is not None:
            detail = f"{detail} (line {exc.parse_error.line_number})"
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "configuration_parse_error", detail
        )

    @app.exception_handler(IncidentNotFoundError)
    async def _incident_not_found(request: Request, exc: IncidentNotFoundError) -> JSONResponse:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "incident_not_found",
            f"Incident '{exc.incident_id}' was not found.",
        )

    @app.exception_handler(DeviceNotFoundError)
    async def _device_not_found(request: Request, exc: DeviceNotFoundError) -> JSONResponse:
        return _error_response(status.HTTP_404_NOT_FOUND, "device_not_found", str(exc))

    @app.exception_handler(DeviceConflictError)
    async def _device_conflict(request: Request, exc: DeviceConflictError) -> JSONResponse:
        return _error_response(status.HTTP_409_CONFLICT, "device_conflict", str(exc))

    @app.exception_handler(SnapshotAlreadyExistsError)
    async def _snapshot_already_exists(
        request: Request, exc: SnapshotAlreadyExistsError
    ) -> JSONResponse:
        return _error_response(status.HTTP_409_CONFLICT, "snapshot_already_exists", str(exc))

    @app.exception_handler(ReferencedDeviceNotFoundError)
    async def _referenced_device_not_found(
        request: Request, exc: ReferencedDeviceNotFoundError
    ) -> JSONResponse:
        return _error_response(status.HTTP_409_CONFLICT, "referenced_device_not_found", str(exc))

    @app.exception_handler(PersistenceError)
    async def _persistence_error(request: Request, exc: PersistenceError) -> JSONResponse:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "persistence_error", _GENERIC_PERSISTENCE_DETAIL
        )

    @app.exception_handler(SerializationError)
    async def _serialization_error(request: Request, exc: SerializationError) -> JSONResponse:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "serialization_error",
            _GENERIC_SERIALIZATION_DETAIL,
        )

    @app.exception_handler(ValueError)
    async def _invalid_request(request: Request, exc: ValueError) -> JSONResponse:
        return _error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_request", str(exc))
