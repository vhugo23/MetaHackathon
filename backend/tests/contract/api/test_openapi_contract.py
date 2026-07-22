"""OpenAPI contract stabilization tests (Day 6A).

Asserts the frontend-facing shape of the generated OpenAPI document
directly — never a full-document snapshot (an incidental, unrelated field
addition should not fail this suite). Each test targets one named,
independently meaningful claim about the document: stable operation IDs,
the exact path set, documented error responses, and the exact success/
request schemas already implemented in ``api/schemas.py``.
"""

from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork


def _openapi_schema() -> dict[str, Any]:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema: dict[str, Any] = response.json()
    return schema


def test_openapi_contract__approved_operation_ids__are_exact() -> None:
    schema = _openapi_schema()

    assert schema["paths"]["/health"]["get"]["operationId"] == "health_check"
    assert (
        schema["paths"]["/devices/{device_id}/config"]["post"]["operationId"]
        == "submit_device_configuration"
    )
    assert schema["paths"]["/incidents"]["get"]["operationId"] == "list_incidents"


def test_openapi_contract__resolve_incident__operation_id_is_exact() -> None:
    schema = _openapi_schema()

    assert (
        schema["paths"]["/incidents/{incident_id}/resolve"]["post"]["operationId"]
        == "resolve_incident"
    )


def test_openapi_contract__paths__are_exactly_the_approved_set() -> None:
    schema = _openapi_schema()

    assert set(schema["paths"].keys()) == {
        "/health",
        "/devices/{device_id}/config",
        "/devices/{device_id}/drift",
        "/incidents",
        "/incidents/{incident_id}/resolve",
    }


def test_openapi_contract__health_response__is_explicitly_represented() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/health"]["get"]["responses"]
    assert "200" in responses
    body_schema = responses["200"]["content"]["application/json"]["schema"]
    assert body_schema  # explicit schema present, not left implicit


def test_openapi_contract__submit_configuration__documents_201() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/config"]["post"]["responses"]
    assert "201" in responses


def test_openapi_contract__submit_configuration__documents_409_with_api_error_response() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/config"]["post"]["responses"]
    assert "409" in responses
    schema_ref = responses["409"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/ApiErrorResponse"


def test_openapi_contract__submit_configuration__documents_422() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/config"]["post"]["responses"]
    assert "422" in responses


def test_openapi_contract__submit_configuration__422_covers_both_runtime_bodies() -> None:
    """FastAPI's own RequestValidationError (HTTPValidationError) and
    application-originated 422s (ApiErrorResponse, e.g. unsupported_vendor/
    configuration_parse_error/invalid_request) are both real runtime bodies
    for this status code (see api/errors.py) — the documented schema must
    not falsely claim only one of them exists."""
    schema = _openapi_schema()

    body_schema = schema["paths"]["/devices/{device_id}/config"]["post"]["responses"]["422"][
        "content"
    ]["application/json"]["schema"]
    refs = {entry["$ref"] for entry in body_schema["oneOf"]}
    assert refs == {
        "#/components/schemas/HTTPValidationError",
        "#/components/schemas/ApiErrorResponse",
    }


def test_openapi_contract__submit_configuration__documents_500_with_api_error_response() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/config"]["post"]["responses"]
    assert "500" in responses
    schema_ref = responses["500"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/ApiErrorResponse"


def test_openapi_contract__submit_configuration__request_schema_forbids_extra_properties() -> None:
    schema = _openapi_schema()

    request_schema = schema["components"]["schemas"]["SubmitConfigurationRequest"]
    assert request_schema["additionalProperties"] is False


def test_openapi_contract__submit_configuration__request_schema_has_expected_fields() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["SubmitConfigurationRequest"]["properties"]
    assert "vendor" in properties
    assert "raw_config_text" in properties


def test_openapi_contract__submit_configuration__request_schema_excludes_device_id() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["SubmitConfigurationRequest"]["properties"]
    assert "device_id" not in properties


def test_openapi_contract__submit_configuration__request_schema_excludes_observed_at() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["SubmitConfigurationRequest"]["properties"]
    assert "observed_at" not in properties


def test_openapi_contract__submit_configuration__success_schema_has_only_approved_fields() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["SubmitConfigurationResponse"]["properties"]
    assert set(properties.keys()) == {
        "device_id",
        "snapshot_id",
        "normalized_config",
        "violations_detected",
        "incidents_created",
        "incidents_updated",
    }


def test_openapi_contract__normalized_routing_response__has_bgp_neighbors_only() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["NormalizedRoutingResponse"]["properties"]
    assert "bgp_neighbors" in properties
    assert "static_routes" not in properties


def test_openapi_contract__list_incidents__returns_array() -> None:
    schema = _openapi_schema()

    body_schema = schema["paths"]["/incidents"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert body_schema["type"] == "array"


def test_openapi_contract__incident_response__contains_fingerprint() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["IncidentResponse"]["properties"]
    assert "fingerprint" in properties


def test_openapi_contract__resolve_incident__documents_200() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/incidents/{incident_id}/resolve"]["post"]["responses"]
    assert "200" in responses
    body_schema = responses["200"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"] == "#/components/schemas/IncidentResponse"


def test_openapi_contract__resolve_incident__documents_404_with_api_error_response() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/incidents/{incident_id}/resolve"]["post"]["responses"]
    assert "404" in responses
    schema_ref = responses["404"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/ApiErrorResponse"


def test_openapi_contract__resolve_incident__has_no_request_body() -> None:
    schema = _openapi_schema()

    operation = schema["paths"]["/incidents/{incident_id}/resolve"]["post"]
    assert "requestBody" not in operation


def test_openapi_contract__resolve_incident__and_list_incidents__share_same_response_schema() -> (
    None
):
    schema = _openapi_schema()

    list_schema = schema["paths"]["/incidents"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    resolve_schema = schema["paths"]["/incidents/{incident_id}/resolve"]["post"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert list_schema["items"]["$ref"] == "#/components/schemas/IncidentResponse"
    assert resolve_schema["$ref"] == "#/components/schemas/IncidentResponse"


def test_openapi_contract__incident_response__has_updated_at_and_resolved_at() -> None:
    schema = _openapi_schema()

    incident_schema = schema["components"]["schemas"]["IncidentResponse"]
    assert "updated_at" in incident_schema["properties"]
    assert "resolved_at" in incident_schema["properties"]
    assert "updated_at" in incident_schema["required"]

    # resolved_at is a required key (always present in the response) but
    # its value is nullable — Pydantic v2's `datetime | None` renders as an
    # `anyOf` including an explicit null type, not `required` absence.
    assert "resolved_at" in incident_schema["required"]
    resolved_at_schema = incident_schema["properties"]["resolved_at"]
    types_in_any_of = {member.get("type") for member in resolved_at_schema["anyOf"]}
    assert "null" in types_in_any_of


def test_openapi_contract__list_incidents__documents_500_with_api_error_response() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/incidents"]["get"]["responses"]
    assert "500" in responses
    schema_ref = responses["500"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/ApiErrorResponse"


def test_openapi_contract__success_schemas__contain_no_data_error_envelope() -> None:
    schema = _openapi_schema()

    for name in ("SubmitConfigurationResponse", "IncidentResponse"):
        properties = schema["components"]["schemas"][name]["properties"]
        assert "data" not in properties
        assert "error" not in properties


def test_openapi_contract__api_error_response__has_exactly_code_and_detail() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["ApiErrorResponse"]["properties"]
    assert set(properties.keys()) == {"code", "detail"}


def test_openapi_contract__get_device_drift__operation_id_is_exact() -> None:
    schema = _openapi_schema()

    assert schema["paths"]["/devices/{device_id}/drift"]["get"]["operationId"] == "get_device_drift"


def test_openapi_contract__get_device_drift__path_parameter_is_required_string() -> None:
    schema = _openapi_schema()

    parameters = schema["paths"]["/devices/{device_id}/drift"]["get"]["parameters"]
    device_id_param = next(p for p in parameters if p["name"] == "device_id")
    assert device_id_param["in"] == "path"
    assert device_id_param["required"] is True
    assert device_id_param["schema"]["type"] == "string"


def test_openapi_contract__get_device_drift__documents_200_with_drift_report_schema() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/drift"]["get"]["responses"]
    assert "200" in responses
    schema_ref = responses["200"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/DriftReportResponse"


def test_openapi_contract__get_device_drift__documents_404_with_api_error_response() -> None:
    schema = _openapi_schema()

    responses = schema["paths"]["/devices/{device_id}/drift"]["get"]["responses"]
    assert "404" in responses
    schema_ref = responses["404"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"] == "#/components/schemas/ApiErrorResponse"


def test_openapi_contract__drift_report_response__has_only_added_removed_changed() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["DriftReportResponse"]["properties"]
    assert set(properties.keys()) == {"added", "removed", "changed"}


def test_openapi_contract__drift_entry_response__has_only_approved_fields() -> None:
    schema = _openapi_schema()

    properties = schema["components"]["schemas"]["DriftEntryResponse"]["properties"]
    assert set(properties.keys()) == {"resource", "field", "old_value", "new_value"}
