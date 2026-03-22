"""OAuth 2.0 Authorization Code flow for Oura Ring.

Usage:
    python3 cli.py auth oura

Config: oura.client_id and oura.client_secret in gateway.yaml.
Tokens are encrypted at rest via TokenStore.
"""

import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

from engine.gateway.token_store import TokenStore

SERVICE_NAME = "oura"

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
DEFAULT_SCOPES = ["daily", "heartrate", "workout", "sleep", "personal"]


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Ephemeral HTTP handler to capture OAuth callback."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Oura Ring connected!</h2>"
                             b"<p>You can close this window.</p></body></html>")
        elif "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authorization failed.</h2>"
                             b"<p>Please try again.</p></body></html>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress noisy request logs


def run_auth_flow(
    client_id: str,
    client_secret: str,
    user_id: str = "default",
    token_store: Optional[TokenStore] = None,
    port: int = 0,
) -> dict:
    """Run interactive OAuth flow with ephemeral local server.

    Args:
        client_id: Oura OAuth client ID.
        client_secret: Oura OAuth client secret.
        user_id: User identifier for multi-user support.
        token_store: Optional TokenStore instance.
        port: Local port for callback (0 = auto-pick).

    Returns:
        Dict with 'authenticated' and token details.
    """
    import webbrowser

    # Reset handler state
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None

    server = HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    actual_port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{actual_port}/callback"

    state = secrets.token_urlsafe(32)

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(DEFAULT_SCOPES),
        "state": state,
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(auth_params)}"

    print(f"Opening browser for Oura authorization...")
    print(f"If the browser doesn't open, visit:\n{auth_url}")
    webbrowser.open(auth_url)

    # Wait for callback (timeout after 5 minutes)
    server.timeout = 300
    while _OAuthCallbackHandler.auth_code is None and _OAuthCallbackHandler.error is None:
        server.handle_request()

    server.server_close()

    if _OAuthCallbackHandler.error:
        return {
            "authenticated": False,
            "error": _OAuthCallbackHandler.error,
        }

    if not _OAuthCallbackHandler.auth_code:
        return {
            "authenticated": False,
            "error": "No authorization code received.",
        }

    # Exchange code for tokens
    token_data = _exchange_code(
        code=_OAuthCallbackHandler.auth_code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    if "error" in token_data:
        return {
            "authenticated": False,
            "error": token_data.get("error_description", token_data["error"]),
        }

    # Save tokens
    store = token_store or TokenStore()
    save_data = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_in": token_data.get("expires_in", 86400),
        "obtained_at": int(time.time()),
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": DEFAULT_SCOPES,
    }

    saved_dir = store.save_token(SERVICE_NAME, user_id, save_data)
    print(f"Oura tokens saved to {saved_dir}")

    return {
        "authenticated": True,
        "user_id": user_id,
        "scopes": DEFAULT_SCOPES,
    }


def run_gateway_auth_flow(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    user_id: str = "default",
    token_store: Optional[TokenStore] = None,
) -> dict:
    """Exchange an authorization code for tokens (gateway-initiated flow).

    Used when the gateway handles the OAuth redirect instead of a local server.
    """
    token_data = _exchange_code(
        code=code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    if "error" in token_data:
        return {
            "authenticated": False,
            "error": token_data.get("error_description", token_data["error"]),
        }

    store = token_store or TokenStore()
    save_data = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "expires_in": token_data.get("expires_in", 86400),
        "obtained_at": int(time.time()),
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": DEFAULT_SCOPES,
    }

    store.save_token(SERVICE_NAME, user_id, save_data)

    return {
        "authenticated": True,
        "user_id": user_id,
    }


def _exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": f"HTTP {e.code}", "error_description": body}
    except Exception as e:
        return {"error": "network_error", "error_description": str(e)}
