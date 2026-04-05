"""Tests for Garmin auth endpoint logging.

Verifies that GET /auth/garmin and POST /auth/garmin/submit emit
structured log lines for every outcome: form served, state invalid,
rate limited, auth success, auth failure.
"""

import hashlib
import hmac as hmac_mod
import logging
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from engine.gateway.server import create_app
from engine.gateway.config import GatewayConfig

HMAC_SECRET = "test-secret-for-garmin-auth-logging"


def _sign_state(user_id: str, service: str) -> str:
    """Generate a valid HMAC state matching the server's _sign_state."""
    bucket = str(int(time.time()) // 3600)
    payload = f"{user_id}:{service}:{bucket}"
    sig = hmac_mod.new(HMAC_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


@pytest.fixture
def client():
    config = GatewayConfig(port=18899, api_token="test-token-123", hmac_secret=HMAC_SECRET)
    app = create_app(config)
    return TestClient(app)


class TestGarminAuthFormLogging:
    """GET /auth/garmin logging tests."""

    def test_form_served_logs_info(self, client, caplog):
        state = _sign_state("mike", "garmin")
        with caplog.at_level(logging.INFO, logger="health-engine.gateway"):
            resp = client.get(f"/auth/garmin?user=mike&state={state}")
        assert resp.status_code == 200
        assert any("garmin_auth_form served user_id=mike" in r.message for r in caplog.records)

    def test_expired_link_logs_warning(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="health-engine.gateway"):
            resp = client.get("/auth/garmin?user=mike&state=mike:garmin:000000:badbadbadbadbadb")
        assert resp.status_code == 403
        assert any("garmin_auth_form state_invalid user=mike" in r.message for r in caplog.records)

    def test_param_mismatch_logs_warning(self, client, caplog):
        """State verifies for oura but endpoint is /auth/garmin."""
        state = _sign_state("mike", "oura")
        with caplog.at_level(logging.WARNING, logger="health-engine.gateway"):
            resp = client.get(f"/auth/garmin?user=mike&state={state}")
        assert resp.status_code == 403
        assert any("param_mismatch" in r.message for r in caplog.records)


class TestGarminAuthSubmitLogging:
    """POST /auth/garmin/submit logging tests."""

    def test_submit_success_logs_attempt_and_result(self, client, caplog):
        state = _sign_state("mike", "garmin")
        with patch("engine.gateway.server._do_garmin_auth", return_value={
            "authenticated": True, "user_id": "mike",
        }):
            with caplog.at_level(logging.INFO, logger="health-engine.gateway"):
                resp = client.post("/auth/garmin/submit", data={
                    "email": "mike@test.com", "password": "pass",
                    "user_id": "mike", "state": state,
                })
        assert resp.status_code == 200
        messages = [r.message for r in caplog.records]
        assert any("garmin_auth_submit attempt user_id=mike" in m for m in messages)
        assert any("garmin_auth_submit success user_id=mike" in m for m in messages)

    def test_submit_invalid_state_logs_warning(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="health-engine.gateway"):
            resp = client.post("/auth/garmin/submit", data={
                "email": "mike@test.com", "password": "pass",
                "user_id": "mike", "state": "mike:garmin:000000:badbadbadbadbadb",
            })
        assert resp.status_code == 403
        assert any("garmin_auth_submit state_invalid" in r.message for r in caplog.records)

    def test_auth_failure_logs_error_type(self, client, caplog):
        state = _sign_state("mike", "garmin")
        with patch("engine.gateway.server._do_garmin_auth", return_value={
            "authenticated": False, "error": "bad creds",
            "error_type": "bad_credentials_401", "rate_limited": False,
        }):
            with caplog.at_level(logging.WARNING, logger="health-engine.gateway"):
                resp = client.post("/auth/garmin/submit", data={
                    "email": "mike@test.com", "password": "wrong",
                    "user_id": "mike", "state": state,
                })
        assert resp.status_code == 200
        assert any("bad_credentials_401" in r.message for r in caplog.records)

    def test_garmin_429_logs_cooldown(self, client, caplog):
        state = _sign_state("mike", "garmin")
        with patch("engine.gateway.server._do_garmin_auth", return_value={
            "authenticated": False, "error": "rate limited",
            "error_type": "rate_limit_429", "rate_limited": True,
        }):
            with caplog.at_level(logging.WARNING, logger="health-engine.gateway"):
                resp = client.post("/auth/garmin/submit", data={
                    "email": "mike@test.com", "password": "pass",
                    "user_id": "mike", "state": state,
                })
        assert resp.status_code == 200
        assert any("garmin_429" in r.message and "consecutive=1" in r.message for r in caplog.records)
