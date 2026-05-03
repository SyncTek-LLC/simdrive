"""Tests for GET /health — Railway healthcheck endpoint.

TDD: written before implementation.
Returns: {status: "ok", version, db_reachable, storage_backend}
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def keypair():
    from simdrive.license.keypair import generate_keypair
    return generate_keypair()


@pytest.fixture(scope="module")
def app(keypair):
    from simdrive.cloud.app import create_app
    sk, vk = keypair
    return create_app(
        _signing_key=sk,
        _verify_key=vk,
        database_url="sqlite://",
    )


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """GET /health endpoint for Railway healthcheck-driven deploys."""

    def test_health_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_status_is_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_has_version_field(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_health_has_db_reachable(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "db_reachable" in data
        assert isinstance(data["db_reachable"], bool)

    def test_health_db_reachable_true_when_db_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        # With SQLite in-memory, DB should be reachable
        assert data["db_reachable"] is True

    def test_health_has_storage_backend(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "storage_backend" in data
        # Should be one of the known backend names
        assert data["storage_backend"] in ("stub", "r2", "r2_stub", "R2Stub", "R2Client", "r2stub", "r2client")

    def test_health_no_auth_required(self, client: TestClient) -> None:
        """Health endpoint must be public — no auth header needed."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_storage_backend_is_stub_in_tests(self, client: TestClient) -> None:
        """In test mode (no R2 env vars), storage_backend should indicate stub."""
        resp = client.get("/health")
        data = resp.json()
        # Without R2 env vars, it should be a stub variant
        backend = data["storage_backend"].lower()
        assert "stub" in backend or backend == "r2stub"
