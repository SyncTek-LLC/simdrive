"""Tests for cloud FastAPI app and all routes.

TDD: written before implementation.
Uses FastAPI TestClient. Covers: trials, licenses, recordings endpoints.
"""
from __future__ import annotations

import json
import time
import base64
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
def valid_license_key(keypair):
    """A valid Pro license key signed with the module-level keypair."""
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk,
        tier="pro",
        seats=4,
        customer_email="cloud@test.com",
        issued_at=now,
        expires_at=now + 86400 * 30,
    )


@pytest.fixture(scope="module")
def expired_license_key(keypair):
    """An expired license key."""
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk,
        tier="solo",
        seats=1,
        customer_email="expired@test.com",
        issued_at=now - 86400 * 30,
        expires_at=now - 86400 * 16,  # expired 16 days ago — past 7-day grace
    )


@pytest.fixture(scope="module")
def app(keypair):
    """Create a TestClient for the cloud app wired with the test keypair.

    Injects both signing_key and verify_key so trial/activate endpoints
    produce keys verifiable by the test verify_key.
    """
    from simdrive.cloud.app import create_app
    sk, vk = keypair
    application = create_app(
        _signing_key=sk,
        _verify_key=vk,
        database_url="sqlite://",  # in-memory for tests
    )
    return application


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# /v1/trials tests
# ---------------------------------------------------------------------------

class TestTrialsRoute:

    def test_post_trial_returns_key(self, client: TestClient, monkeypatch) -> None:
        """POST /v1/trials should return a license key."""
        # Patch the trial generator to avoid needing a real signing key in cloud
        resp = client.post("/v1/trials", json={"email": "new@user.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data
        assert "expires_at" in data

    def test_post_trial_missing_email_fails(self, client: TestClient) -> None:
        resp = client.post("/v1/trials", json={})
        assert resp.status_code == 422  # Unprocessable Entity

    def test_post_trial_invalid_email_fails(self, client: TestClient) -> None:
        resp = client.post("/v1/trials", json={"email": "notanemail"})
        assert resp.status_code == 422

    def test_post_trial_key_has_two_parts(self, client: TestClient) -> None:
        resp = client.post("/v1/trials", json={"email": "parts@test.com"})
        assert resp.status_code == 200
        key = resp.json()["key"]
        assert len(key.split(".")) == 2

    def test_post_trial_expires_14_days(self, client: TestClient) -> None:
        resp = client.post("/v1/trials", json={"email": "exp@test.com"})
        assert resp.status_code == 200
        data = resp.json()
        expires_at = data["expires_at"]
        now = time.time()
        # Should expire ~14 days from now (allow 1-day tolerance)
        diff = expires_at - now
        assert 86400 * 13 <= diff <= 86400 * 15


# ---------------------------------------------------------------------------
# /v1/licenses/activate tests
# ---------------------------------------------------------------------------

class TestLicensesActivateRoute:

    def test_activate_with_fixture_payload(self, client: TestClient) -> None:
        """POST /v1/licenses/activate with a fixture Stripe-like payload."""
        resp = client.post("/v1/licenses/activate", json={
            "stripe_subscription_id": "sub_test_12345",
            "email": "paying@customer.com",
            "tier": "pro",
            "seats": 4,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data
        assert data["tier"] == "pro"
        assert data["seats"] == 4

    def test_activate_missing_email_fails(self, client: TestClient) -> None:
        resp = client.post("/v1/licenses/activate", json={
            "stripe_subscription_id": "sub_test_99",
        })
        assert resp.status_code == 422

    def test_activate_invalid_tier_fails(self, client: TestClient) -> None:
        resp = client.post("/v1/licenses/activate", json={
            "stripe_subscription_id": "sub_test_99",
            "email": "a@b.com",
            "tier": "platinum",  # invalid
            "seats": 1,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /v1/licenses/status tests
# ---------------------------------------------------------------------------

class TestLicensesStatusRoute:

    def test_status_valid_key(self, client: TestClient, valid_license_key: str) -> None:
        resp = client.get(f"/v1/licenses/status?key={valid_license_key}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert "expires_at" in data
        assert "server_time" in data

    def test_status_returns_server_time(self, client: TestClient, valid_license_key: str) -> None:
        before = int(time.time())
        resp = client.get(f"/v1/licenses/status?key={valid_license_key}")
        after = int(time.time())
        server_time = resp.json()["server_time"]
        assert before <= server_time <= after + 2

    def test_status_expired_key(self, client: TestClient, expired_license_key: str) -> None:
        resp = client.get(f"/v1/licenses/status?key={expired_license_key}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False

    def test_status_invalid_key(self, client: TestClient) -> None:
        resp = client.get("/v1/licenses/status?key=garbage.signature")
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_status_missing_key_fails(self, client: TestClient) -> None:
        resp = client.get("/v1/licenses/status")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /v1/recordings tests
# ---------------------------------------------------------------------------

class TestRecordingsRoute:

    def _auth_headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def test_post_recording_creates_entry(self, client: TestClient, valid_license_key: str) -> None:
        resp = client.post(
            "/v1/recordings",
            headers=self._auth_headers(valid_license_key),
            json={
                "license_key": valid_license_key,
                "recording_yaml": "steps:\n  - action: tap",
                "screenshots": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "recording_id" in data
        assert "url" in data

    def test_post_recording_no_auth_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            json={
                "recording_yaml": "steps: []",
                "screenshots": [],
            },
        )
        assert resp.status_code in (401, 403, 422)

    def test_post_recording_expired_key_fails(self, client: TestClient, expired_license_key: str) -> None:
        resp = client.post(
            "/v1/recordings",
            headers=self._auth_headers(expired_license_key),
            json={
                "license_key": expired_license_key,
                "recording_yaml": "steps: []",
                "screenshots": [],
            },
        )
        assert resp.status_code in (401, 403)

    def test_post_recording_returns_recording_id(self, client: TestClient, valid_license_key: str) -> None:
        resp = client.post(
            "/v1/recordings",
            headers=self._auth_headers(valid_license_key),
            json={
                "license_key": valid_license_key,
                "recording_yaml": "name: test\nsteps:\n  - action: tap",
                "screenshots": ["aGVsbG8="],  # base64 of "hello"
            },
        )
        assert resp.status_code == 200
        rid = resp.json()["recording_id"]
        assert len(rid) > 0

    def test_post_recording_bad_auth_format(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": "Token notbearer"},
            json={
                "recording_yaml": "steps: []",
                "screenshots": [],
            },
        )
        assert resp.status_code in (401, 403, 422)
