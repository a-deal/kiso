"""Twilio SMS webhook handler and outbound SMS helper.

Receives inbound SMS via POST /api/webhooks/twilio, verifies the
Twilio signature, routes the message to the appropriate user, and
forwards it to OpenClaw for agent processing.

Outbound SMS is available via send_sms() and registered as a tool
in TOOL_REGISTRY so the coaching agent can reply via SMS.
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from fastapi import Request
from fastapi.responses import Response

logger = logging.getLogger("health-engine.twilio")

_AUDIT_LOG_PATH = os.path.join("data", "admin", "api_audit.jsonl")

# Phone numbers that must NOT receive any outbound from this module.
# Added 2026-04-13 (architecture audit session). See:
#   hub/plans/audit-2026-04-13/ground-truth.md
# Checked in BOTH `send_sms` (direct outbound / agent tool path) AND
# `_forward_to_openclaw` (inbound-reply path), since neither consults
# person.channel. Removing an entry here re-enables every outbound path
# in this file for that phone — update the audit context at the same time.
#
# NOT covered by this list: voice_bridge.py (outbound voice calls, if any)
# and any other send helper outside twilio_sms. If that surface exists,
# it needs its own mute check or this should be lifted to a single chokepoint.
_MUTED_PHONES: set[str] = {
    "+17038878948",  # Paul — muted during cleanup audit, pending proper resolution
}


def _audit_log(action: str, user_id: str, params: dict,
               error: str | None = None, elapsed_ms: int = 0):
    """Append audit entry for SMS events."""
    entry = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "tool": f"twilio_sms:{action}",
        "user_id": user_id,
        "params": {k: v for k, v in params.items()
                   if k not in ("token", "auth_token", "AccountSid")},
        "status": "ok" if error is None else "error",
        "ms": elapsed_ms,
    }
    if error is not None:
        entry["error"] = str(error)
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG_PATH), exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        logger.warning("Failed to write SMS audit log", exc_info=True)


def _verify_twilio_signature(auth_token: str, url: str,
                              params: dict, signature: str) -> bool:
    """Verify X-Twilio-Signature header (HMAC-SHA1).

    See: https://www.twilio.com/docs/usage/security#validating-requests
    """
    # Build the data string: URL + sorted POST params
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]

    expected = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    import base64
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)


def _lookup_user_by_phone(phone: str) -> str | None:
    """Look up user_id from phone number.

    Checks data/users/*/config.yaml for phone fields, and also
    checks the OpenClaw users.yaml phone mapping.
    """
    from pathlib import Path

    # Normalize phone: ensure +country code
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    # Check OpenClaw users.yaml
    users_yaml = Path(os.path.expanduser("~/.openclaw/workspace/users.yaml"))
    if users_yaml.exists():
        import yaml
        with open(users_yaml) as f:
            data = yaml.safe_load(f) or {}
        for entry_phone, info in data.get("users", {}).items():
            if entry_phone.strip() == phone:
                return info.get("user_id")

    # Check per-user config directories
    users_dir = Path(__file__).parent.parent.parent / "data" / "users"
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            config_file = user_dir / "config.yaml"
            if config_file.exists():
                import yaml
                with open(config_file) as f:
                    cfg = yaml.safe_load(f) or {}
                profile_phone = cfg.get("profile", {}).get("phone", "")
                if profile_phone.strip() == phone:
                    return user_dir.name

    return None


def send_sms(to: str, body: str, user_id: str = "",
             account_sid: str = "", auth_token: str = "",
             from_number: str = "") -> dict:
    """Send an SMS via Twilio REST API.

    Returns dict with message SID on success, or error details.
    """
    t0 = time.time()

    # Normalize phone
    to = to.strip()
    if not to.startswith("+"):
        to = "+" + to

    # Mute list: drop outbound to phones we've explicitly silenced.
    # See _MUTED_PHONES comment at top of module.
    if to in _MUTED_PHONES:
        logger.warning(
            "twilio_sms.send_sms: dropping outbound to muted phone %s (user_id=%s)",
            to, user_id,
        )
        _audit_log("send_muted", user_id, {
            "to": to,
            "body_len": len(body),
            "reason": "phone in _MUTED_PHONES",
        })
        return {"status": "muted", "to": to}

    if not account_sid or not auth_token or not from_number:
        error = "Twilio credentials not configured in gateway.yaml"
        _audit_log("send", user_id, {"to": to}, error=error)
        return {"status": "error", "error": error}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    try:
        resp = requests.post(
            url,
            data={"From": from_number, "To": to, "Body": body},
            auth=(account_sid, auth_token),
            timeout=10,
        )
        result = resp.json()
        elapsed = int((time.time() - t0) * 1000)

        if resp.status_code in (200, 201):
            _audit_log("send", user_id,
                       {"to": to, "body_len": len(body)},
                       elapsed_ms=elapsed)
            return {
                "status": "ok",
                "message_sid": result.get("sid"),
                "to": to,
            }
        else:
            error = result.get("message", f"HTTP {resp.status_code}")
            _audit_log("send", user_id,
                       {"to": to, "body_len": len(body)},
                       error=error, elapsed_ms=elapsed)
            return {"status": "error", "error": error, "code": result.get("code")}

    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        _audit_log("send", user_id, {"to": to}, error=str(e), elapsed_ms=elapsed)
        return {"status": "error", "error": str(e)}


def _forward_to_openclaw(from_number: str, body: str, user_id: str):
    """Forward inbound SMS to OpenClaw agent for processing.

    Uses openclaw agent CLI to inject the message into the agent session,
    then delivers the reply back via SMS.

    Phones listed in _MUTED_PHONES short-circuit before any outbound call.
    """
    if from_number in _MUTED_PHONES:
        logger.warning(
            "twilio_sms: dropping inbound forward for muted phone %s (user_id=%s)",
            from_number, user_id,
        )
        _audit_log("inbound_muted", user_id or from_number, {
            "from": from_number,
            "reason": "phone in _MUTED_PHONES",
        })
        return

    try:
        result = subprocess.run(
            [
                "openclaw", "agent",
                "--to", from_number,
                "--channel", "sms",
                "--message", body,
                "--deliver",
                "--json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("openclaw agent failed: %s", result.stderr)
    except FileNotFoundError:
        logger.warning("openclaw CLI not found — SMS forwarding unavailable")
    except subprocess.TimeoutExpired:
        logger.error("openclaw agent timed out for SMS from %s", from_number)
    except Exception as e:
        logger.error("Failed to forward SMS to openclaw: %s", e)


def create_twilio_webhook(config):
    """Create the Twilio webhook handler bound to gateway config.

    Returns the async handler function to be registered as a route.
    """
    twilio_cfg = getattr(config, "twilio", {}) or {}
    auth_token = twilio_cfg.get("auth_token", "")
    account_sid = twilio_cfg.get("account_sid", "")
    from_number = twilio_cfg.get("from_number", "")

    async def twilio_webhook(request: Request):
        """Handle inbound SMS from Twilio."""
        t0 = time.time()

        # Parse form data
        form = await request.form()
        params = {k: v for k, v in form.items()}

        from_phone = params.get("From", "")
        to_phone = params.get("To", "")
        body = params.get("Body", "")
        message_sid = params.get("MessageSid", "")

        # Verify Twilio signature
        if auth_token:
            signature = request.headers.get("X-Twilio-Signature", "")
            # Reconstruct the full URL Twilio used
            webhook_url = str(request.url).split("?")[0]
            if config.tunnel_domain:
                webhook_url = f"https://{config.tunnel_domain}/api/webhooks/twilio"

            if not _verify_twilio_signature(auth_token, webhook_url,
                                            params, signature):
                logger.warning("Invalid Twilio signature from %s", from_phone)
                _audit_log("inbound_rejected", "",
                           {"from": from_phone, "reason": "invalid_signature"})
                return Response(
                    content='<Response></Response>',
                    media_type="application/xml",
                    status_code=403,
                )

        # Look up user
        user_id = _lookup_user_by_phone(from_phone) or ""
        elapsed = int((time.time() - t0) * 1000)

        _audit_log("inbound", user_id or from_phone, {
            "from": from_phone,
            "to": to_phone,
            "body_len": len(body),
            "message_sid": message_sid,
        }, elapsed_ms=elapsed)

        logger.info("SMS from %s (user=%s): %s", from_phone,
                     user_id or "unknown", body[:100])

        # Forward to OpenClaw agent (fire and forget in background)
        threading.Thread(
            target=_forward_to_openclaw,
            args=(from_phone, body, user_id),
            daemon=True,
        ).start()

        # Return empty TwiML — replies go via Twilio REST API
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml",
        )

    return twilio_webhook


def _send_sms_tool(to: str = "", message: str = "",
                    user_id: str = "") -> dict:
    """Send an SMS to a user. Tool wrapper for TOOL_REGISTRY.

    If user_id is provided without 'to', looks up the phone number
    from the user registry.
    """
    from .config import load_gateway_config
    config = load_gateway_config()
    twilio_cfg = getattr(config, "twilio", {}) or {}

    account_sid = twilio_cfg.get("account_sid", "")
    auth_token = twilio_cfg.get("auth_token", "")
    from_number = twilio_cfg.get("from_number", "")

    # If no 'to' number, look up from user_id
    if not to and user_id:
        import yaml
        from pathlib import Path
        users_yaml = Path(os.path.expanduser("~/.openclaw/workspace/users.yaml"))
        if users_yaml.exists():
            with open(users_yaml) as f:
                data = yaml.safe_load(f) or {}
            for phone, info in data.get("users", {}).items():
                if info.get("user_id") == user_id:
                    to = phone
                    break

    if not to:
        return {"status": "error", "error": "No phone number provided or found for user"}

    if not message:
        return {"status": "error", "error": "No message provided"}

    return send_sms(
        to=to, body=message, user_id=user_id,
        account_sid=account_sid, auth_token=auth_token,
        from_number=from_number,
    )
