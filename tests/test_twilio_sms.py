"""Tests for Twilio SMS webhook handler and outbound SMS.

Covers:
- Signature verification (HMAC-SHA1)
- Phone-to-user lookup
- Outbound SMS via REST API
- Inbound webhook handler (FastAPI TestClient)
- Webhook wired into server.py
"""

import base64
import hashlib
import hmac
import os
from unittest.mock import MagicMock, patch

import pytest

from engine.gateway.twilio_sms import (
    _verify_twilio_signature,
    _lookup_user_by_phone,
    send_sms,
    create_twilio_webhook,
)


# --- Signature verification ---


class TestSignatureVerification:
    """Test Twilio X-Twilio-Signature HMAC-SHA1 verification."""

    def _sign(self, auth_token: str, url: str, params: dict) -> str:
        data = url
        for key in sorted(params.keys()):
            data += key + params[key]
        digest = hmac.new(
            auth_token.encode(), data.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(digest).decode()

    def test_valid_signature(self):
        token = "test_auth_token_12345"
        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        params = {
            "From": "+18312917892",
            "To": "+16508897482",
            "Body": "Hello Milo",
            "MessageSid": "SM123abc",
        }
        sig = self._sign(token, url, params)
        assert _verify_twilio_signature(token, url, params, sig) is True

    def test_invalid_signature(self):
        token = "test_auth_token_12345"
        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        params = {"From": "+18312917892", "Body": "Hello"}
        assert _verify_twilio_signature(token, url, params, "badsig") is False

    def test_tampered_params(self):
        token = "test_auth_token_12345"
        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        params = {"From": "+18312917892", "Body": "Hello"}
        sig = self._sign(token, url, params)
        params["Body"] = "Tampered"
        assert _verify_twilio_signature(token, url, params, sig) is False

    def test_empty_params(self):
        token = "test_auth_token_12345"
        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        params = {}
        sig = self._sign(token, url, params)
        assert _verify_twilio_signature(token, url, params, sig) is True


# --- Phone lookup ---


class TestUserLookup:

    def test_unknown_number(self):
        result = _lookup_user_by_phone("+19999999999")
        assert result is None

    def test_normalizes_number(self):
        """Should handle numbers without leading +."""
        result = _lookup_user_by_phone("19999999999")
        assert result is None  # Unknown, but shouldn't crash


# --- Outbound SMS ---


class TestSendSms:

    def test_missing_credentials(self):
        result = send_sms(to="+18312917892", body="test")
        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_send_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"sid": "SM123abc"}

        with patch("engine.gateway.twilio_sms.requests.post", return_value=mock_resp):
            result = send_sms(
                to="+14155551234",
                body="Test message",
                user_id="andrew",
                account_sid="ACtest",
                auth_token="tok123",
                from_number="+16505551234",
            )

        assert result["status"] == "ok"
        assert result["message_sid"] == "SM123abc"

    def test_normalizes_phone(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"sid": "SM456"}

        with patch("engine.gateway.twilio_sms.requests.post", return_value=mock_resp) as mock_post:
            send_sms(
                to="14155551234",  # no + prefix
                body="hi",
                user_id="test",
                account_sid="ACtest",
                auth_token="tok",
                from_number="+16505551234",
            )

        call_data = mock_post.call_args
        assert call_data.kwargs["data"]["To"] == "+14155551234"

    def test_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "message": "Invalid phone number",
            "code": 21211,
        }

        with patch("engine.gateway.twilio_sms.requests.post", return_value=mock_resp):
            result = send_sms(
                to="+14155551234",
                body="Test",
                user_id="test",
                account_sid="ACtest",
                auth_token="tok",
                from_number="+16505551234",
            )

        assert result["status"] == "error"
        assert "Invalid phone number" in result["error"]

    def test_network_error(self):
        with patch(
            "engine.gateway.twilio_sms.requests.post",
            side_effect=ConnectionError("Network down"),
        ):
            result = send_sms(
                to="+14155551234",
                body="Test",
                user_id="test",
                account_sid="ACtest",
                auth_token="tok",
                from_number="+16505551234",
            )

        assert result["status"] == "error"
        assert "Network down" in result["error"]


# --- Inbound webhook ---


class TestInboundWebhook:
    """Test the FastAPI webhook handler via TestClient."""

    def _make_app(self, auth_token="test_token", account_sid="ACtest", from_number="+16505551234"):
        from fastapi import FastAPI

        app = FastAPI()
        config = MagicMock()
        config.twilio = {
            "auth_token": auth_token,
            "account_sid": account_sid,
            "from_number": from_number,
        }
        config.tunnel_domain = "auth.mybaseline.health"

        handler = create_twilio_webhook(config)
        app.post("/api/webhooks/twilio")(handler)
        return app

    def _sign(self, auth_token: str, url: str, params: dict) -> str:
        data = url
        for key in sorted(params.keys()):
            data += key + params[key]
        digest = hmac.new(
            auth_token.encode(), data.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(digest).decode()

    def test_valid_inbound_sms(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        client = TestClient(app)

        params = {
            "From": "+14155551234",
            "To": "+16505551234",
            "Body": "How did I sleep?",
            "MessageSid": "SM789",
        }

        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        sig = self._sign("test_token", url, params)

        with patch("engine.gateway.twilio_sms._lookup_user_by_phone", return_value="andrew"):
            with patch("engine.gateway.twilio_sms.threading"):
                resp = client.post(
                    "/api/webhooks/twilio",
                    data=params,
                    headers={"X-Twilio-Signature": sig},
                )

        assert resp.status_code == 200
        assert "<Response>" in resp.text

    def test_invalid_signature_returns_403(self):
        from fastapi.testclient import TestClient

        app = self._make_app()
        client = TestClient(app)

        params = {
            "From": "+14155551234",
            "To": "+16505551234",
            "Body": "hello",
            "MessageSid": "SM789",
        }

        resp = client.post(
            "/api/webhooks/twilio",
            data=params,
            headers={"X-Twilio-Signature": "invalidsig"},
        )

        assert resp.status_code == 403

    def test_no_auth_token_skips_verification(self):
        """When auth_token is empty, signature check is skipped."""
        from fastapi.testclient import TestClient

        app = self._make_app(auth_token="")
        client = TestClient(app)

        params = {
            "From": "+14155551234",
            "To": "+16505551234",
            "Body": "hello",
            "MessageSid": "SM789",
        }

        with patch("engine.gateway.twilio_sms._lookup_user_by_phone", return_value=None):
            with patch("engine.gateway.twilio_sms.threading"):
                resp = client.post(
                    "/api/webhooks/twilio",
                    data=params,
                )

        assert resp.status_code == 200

    def test_forwards_to_openclaw(self):
        """Valid SMS should fire off openclaw forwarding in a background thread."""
        from fastapi.testclient import TestClient

        app = self._make_app()
        client = TestClient(app)

        params = {
            "From": "+14155551234",
            "To": "+16505551234",
            "Body": "Log my workout",
            "MessageSid": "SM999",
        }

        url = "https://auth.mybaseline.health/api/webhooks/twilio"
        sig = self._sign("test_token", url, params)

        with patch("engine.gateway.twilio_sms._lookup_user_by_phone", return_value="andrew"):
            with patch("engine.gateway.twilio_sms.threading.Thread") as mock_thread:
                resp = client.post(
                    "/api/webhooks/twilio",
                    data=params,
                    headers={"X-Twilio-Signature": sig},
                )

        assert resp.status_code == 200
        # Verify thread was started with the right args
        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args
        assert call_kwargs.kwargs["target"].__name__ == "_forward_to_openclaw"
        assert "+14155551234" in call_kwargs.kwargs["args"]


# --- Webhook registration in server.py ---


class TestWebhookRegistration:
    """Verify the Twilio webhook is wired into the gateway server."""

    def test_twilio_webhook_route_exists(self):
        """POST /api/webhooks/twilio must be registered on the gateway app."""
        from engine.gateway.server import create_app

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            app = create_app()

        routes = [r.path for r in app.routes]
        assert "/api/webhooks/twilio" in routes, (
            f"Twilio webhook not registered. Routes: {routes}"
        )
