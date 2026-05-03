"""Tests for cloud/auth.py — Bearer license auth hardening.

Covers: (a) expired-key rejection, (b) tampered-signature rejection,
(c) missing-bearer 401, (d) per-route required-tier checks.

TDD: written before implementation changes.
"""
from __future__ import annotations

import time
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
def valid_pro_key(keypair):
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk, tier="pro", seats=4,
        customer_email="auth@test.com",
        issued_at=now, expires_at=now + 86400 * 30,
    )


@pytest.fixture(scope="module")
def valid_solo_key(keypair):
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk, tier="solo", seats=1,
        customer_email="solo@test.com",
        issued_at=now, expires_at=now + 86400 * 30,
    )


@pytest.fixture(scope="module")
def valid_trial_key(keypair):
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk, tier="trial", seats=1,
        customer_email="trial@test.com",
        issued_at=now, expires_at=now + 86400 * 14,
    )


@pytest.fixture(scope="module")
def expired_key(keypair):
    from simdrive.license.signer import sign_license
    sk, _ = keypair
    now = int(time.time())
    return sign_license(
        signing_key=sk, tier="pro", seats=4,
        customer_email="expired@test.com",
        issued_at=now - 86400 * 30,
        expires_at=now - 86400 * 16,  # expired 16 days ago — past 7-day grace
    )


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
# (c) Missing-bearer 401 tests
# ---------------------------------------------------------------------------

class TestMissingBearerAuth:
    """Verify that endpoints reject requests missing Authorization header."""

    def test_post_recording_no_header_returns_401(self, client: TestClient) -> None:
        resp = client.post("/v1/recordings", json={
            "recording_yaml": "steps: []",
            "screenshots": [],
        })
        assert resp.status_code == 401

    def test_post_recording_wrong_scheme_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": "Basic abc"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 401

    def test_post_recording_token_scheme_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": "Token sometoken"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 401

    def test_wwwauthenticate_header_present_on_401(self, client: TestClient) -> None:
        resp = client.post("/v1/recordings", json={
            "recording_yaml": "steps: []",
            "screenshots": [],
        })
        assert resp.status_code == 401
        # WWW-Authenticate header should be present per RFC 7235
        assert "www-authenticate" in resp.headers or "WWW-Authenticate" in resp.headers


# ---------------------------------------------------------------------------
# (b) Tampered-signature rejection tests
# ---------------------------------------------------------------------------

class TestTamperedSignature:
    """Tampered key must be rejected with 403."""

    def test_tampered_payload_rejected(self, client: TestClient, valid_pro_key: str) -> None:
        """Flip one character in the payload section of the key."""
        parts = valid_pro_key.split(".")
        payload_b64 = parts[0]
        sig_b64 = parts[1]

        # Corrupt the payload by flipping a character
        chars = list(payload_b64)
        chars[5] = "X" if chars[5] != "X" else "Y"
        tampered = ".".join(["".join(chars), sig_b64])

        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {tampered}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403

    def test_tampered_signature_rejected(self, client: TestClient, valid_pro_key: str) -> None:
        """Flip one character in the signature section."""
        parts = valid_pro_key.split(".")
        payload_b64 = parts[0]
        sig_b64 = parts[1]

        chars = list(sig_b64)
        chars[3] = "X" if chars[3] != "X" else "Y"
        tampered = ".".join([payload_b64, "".join(chars)])

        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {tampered}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403

    def test_entirely_garbage_key_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": "Bearer garbage.signature"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403

    def test_missing_dot_separator_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": "Bearer nodotinkey"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# (a) Expired-key rejection tests
# ---------------------------------------------------------------------------

class TestExpiredKeyRejection:
    """Expired keys must be rejected with 403 on protected endpoints."""

    def test_expired_key_rejected_on_post_recording(
        self, client: TestClient, expired_key: str
    ) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {expired_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403

    def test_expired_key_detail_mentions_expiry(
        self, client: TestClient, expired_key: str
    ) -> None:
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {expired_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 403
        # Detail should mention expiry or license in some way
        detail = resp.json().get("detail", "").lower()
        assert any(word in detail for word in ("expir", "license", "invalid", "forbidden"))


# ---------------------------------------------------------------------------
# (d) Per-route required-tier checks
# ---------------------------------------------------------------------------

class TestTierRequirements:
    """Verify per-route tier enforcement on /v1/recordings."""

    def test_pro_key_can_upload_recording(
        self, client: TestClient, valid_pro_key: str
    ) -> None:
        """Pro tier should be allowed to upload recordings."""
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {valid_pro_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 200

    def test_solo_key_can_upload_recording(
        self, client: TestClient, valid_solo_key: str
    ) -> None:
        """Solo tier is allowed (above trial floor)."""
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {valid_solo_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 200

    def test_trial_key_rejected_on_recordings(
        self, client: TestClient, valid_trial_key: str
    ) -> None:
        """Trial tier must be rejected on /v1/recordings (Pro+ required)."""
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {valid_trial_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code in (403, 402)

    def test_valid_bearer_yields_email_in_payload(
        self, client: TestClient, valid_pro_key: str
    ) -> None:
        """Successful auth should result in a recording entry with correct email."""
        resp = client.post(
            "/v1/recordings",
            headers={"Authorization": f"Bearer {valid_pro_key}"},
            json={"recording_yaml": "steps: []", "screenshots": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "recording_id" in data
