"""CORS contract tests (Day 6A).

``create_app(cors_allowed_origins=...)`` is disabled by default (empty
tuple) — every test that wants CORS enabled must say so explicitly. Each
test builds its own isolated ``create_app(...)`` instance, per the Day 5B
binding correction already followed by every other contract test file.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.api.cors import parse_cors_allowed_origins
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _test_app(*, cors_allowed_origins: tuple[str, ...] = ()) -> TestClient:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
        cors_allowed_origins=cors_allowed_origins,
    )
    return TestClient(app)


def test_cors_api__allowed_origin__receives_access_control_allow_origin() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.get("/incidents", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_api__allowed_post_preflight__succeeds() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.options(
        "/devices/smoke-spine-01/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_api__preflight_requests_post_with_content_type() -> None:
    """The preflight itself asserts the request it is standing in for is a
    POST carrying a Content-Type header — the shape the real
    SubmitConfigurationRequest submission uses."""
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.options(
        "/devices/smoke-spine-01/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    allow_headers = response.headers["access-control-allow-headers"]
    assert "content-type" in allow_headers.lower()


def test_cors_api__configured_allow_methods__support_get_post_options_only() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.options(
        "/devices/smoke-spine-01/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    allow_methods = {
        method.strip() for method in response.headers["access-control-allow-methods"].split(",")
    }
    assert allow_methods == {"GET", "POST", "OPTIONS"}


def test_cors_api__unconfigured_origin__receives_no_allow_origin_header() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.get("/incidents", headers={"Origin": "http://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_api__no_origins_configured__no_cors_allow_origin_header() -> None:
    client = _test_app(cors_allowed_origins=())

    response = client.get("/incidents", headers={"Origin": "http://localhost:5173"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_api__health_still_returns_exact_body_with_cors_enabled() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_api__health_does_not_require_the_clock() -> None:
    calls: list[int] = []

    def spy_clock() -> None:
        calls.append(1)

    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=spy_clock,  # type: ignore[arg-type]
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)

    client.get("/health")

    assert calls == []


def test_cors_api__list_incidents_does_not_require_the_clock() -> None:
    calls: list[int] = []

    def spy_clock() -> None:
        calls.append(1)

    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=spy_clock,  # type: ignore[arg-type]
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)

    client.get("/incidents")

    assert calls == []


def test_cors_api__parsing_trims_delimiter_whitespace() -> None:
    origins = parse_cors_allowed_origins(" http://localhost:5173 , http://localhost:4173 ")

    assert origins == ("http://localhost:5173", "http://localhost:4173")


def test_cors_api__parsing_ignores_empty_comma_separated_entries() -> None:
    origins = parse_cors_allowed_origins("http://localhost:5173,,  ,http://localhost:4173")

    assert origins == ("http://localhost:5173", "http://localhost:4173")


def test_cors_api__parsing_empty_string__returns_empty_tuple() -> None:
    assert parse_cors_allowed_origins("") == ()


def test_cors_api__configured_middleware__introduces_no_wildcard() -> None:
    client = _test_app(cors_allowed_origins=("http://localhost:5173",))

    response = client.get("/incidents", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] != "*"
