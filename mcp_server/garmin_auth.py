"""Browser-based Garmin auth flow.

Opens a local page where the user types credentials directly in their browser.
Credentials never pass through the LLM conversation — they go straight from
the browser form to garth, which exchanges them for cached tokens.
"""

import json
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

# Port for the ephemeral local auth server
AUTH_PORT = 18765
AUTH_TIMEOUT = 120  # seconds to wait for user to submit


_auth_result = None
_server_ref = None


AUTH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Health Engine — Garmin Connect</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'DM Sans', sans-serif;
    background: #09090b; color: #fafafa;
    min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
  }
  .card {
    background: #111113; border: 1px solid #27272a;
    border-radius: 16px; padding: 40px;
    width: 100%; max-width: 400px;
  }
  h1 {
    font-size: 1.2rem; font-weight: 600;
    margin-bottom: 6px;
  }
  .subtitle {
    font-size: 0.8rem; color: #71717a;
    margin-bottom: 28px; line-height: 1.5;
  }
  .security-note {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem; color: #52525b;
    background: rgba(34, 197, 94, 0.05);
    border: 1px solid rgba(34, 197, 94, 0.1);
    border-radius: 8px; padding: 10px 12px;
    margin-bottom: 24px; line-height: 1.6;
  }
  .security-note strong { color: #22c55e; }
  label {
    display: block; font-size: 0.75rem;
    color: #a1a1aa; margin-bottom: 6px;
    font-weight: 500;
  }
  input {
    width: 100%; padding: 10px 14px;
    background: #18181b; border: 1px solid #27272a;
    border-radius: 8px; color: #fafafa;
    font-family: 'DM Sans', sans-serif; font-size: 0.9rem;
    margin-bottom: 16px; outline: none;
    transition: border-color 0.15s;
  }
  input:focus { border-color: #3b82f6; }
  button {
    width: 100%; padding: 12px;
    background: #fafafa; color: #09090b;
    border: none; border-radius: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem; font-weight: 600;
    cursor: pointer; transition: opacity 0.15s;
  }
  button:hover { opacity: 0.9; }
  button:disabled { opacity: 0.5; cursor: wait; }
  .status {
    margin-top: 16px; font-size: 0.8rem;
    text-align: center; min-height: 20px;
  }
  .status.error { color: #ef4444; }
  .status.success { color: #22c55e; }
  .status.loading { color: #a1a1aa; }
</style>
</head>
<body>
<div class="card">
  <h1>Connect Garmin</h1>
  <p class="subtitle">Sign in to your Garmin Connect account to sync health data.</p>
  <div class="security-note">
    <strong>Your credentials stay here.</strong> They are sent directly from this
    page to a local server on your machine, used once to obtain session tokens,
    and immediately discarded. Nothing is stored or sent to any cloud service.
  </div>
  <form id="authForm">
    <label for="email">Garmin Connect Email</label>
    <input type="email" id="email" name="email" required autocomplete="email" autofocus>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required autocomplete="current-password">
    <button type="submit" id="submitBtn">Authenticate</button>
  </form>
  <div class="status" id="status"></div>
</div>
<script>
  const form = document.getElementById('authForm');
  const btn = document.getElementById('submitBtn');
  const status = document.getElementById('status');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    btn.textContent = 'Authenticating...';
    status.className = 'status loading';
    status.textContent = 'Connecting to Garmin...';

    try {
      const resp = await fetch('/auth/submit', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams(new FormData(form)),
      });
      const data = await resp.json();
      if (data.authenticated) {
        status.className = 'status success';
        status.textContent = 'Connected! You can close this tab.';
        btn.textContent = 'Done';
        form.querySelectorAll('input').forEach(i => { i.value = ''; i.disabled = true; });
      } else {
        status.className = 'status error';
        status.textContent = data.error || 'Authentication failed. Check credentials.';
        btn.disabled = false;
        btn.textContent = 'Authenticate';
      }
    } catch (err) {
      status.className = 'status error';
      status.textContent = 'Connection error: ' + err.message;
      btn.disabled = false;
      btn.textContent = 'Authenticate';
    }
  });
</script>
</body>
</html>"""


SUCCESS_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Connected</title>
<style>
  body { font-family: sans-serif; background: #09090b; color: #22c55e;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; }
  .msg { text-align: center; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; }
  p { color: #a1a1aa; font-size: 0.9rem; }
</style></head>
<body><div class="msg"><h1>Garmin Connected</h1>
<p>Tokens cached. You can close this tab.</p></div></body></html>"""


class AuthHandler(BaseHTTPRequestHandler):
    """Handles the local auth form and credential submission."""

    token_dir = None

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_GET(self):
        if self.path == "/auth" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(AUTH_PAGE.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _auth_result
        if self.path == "/auth/submit":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = parse_qs(body)
            email = params.get("email", [""])[0]
            password = params.get("password", [""])[0]

            result = _do_garmin_auth(email, password, self.token_dir)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

            # Only signal completion on success — keep server alive for retries
            if result.get("authenticated"):
                _auth_result = result
        else:
            self.send_response(404)
            self.end_headers()


def _do_garmin_auth(email: str, password: str, token_dir: str) -> dict:
    """Authenticate with garth and cache tokens."""
    try:
        from garminconnect import Garmin
        client = Garmin(email, password)
        client.login()
        td = Path(token_dir)
        td.mkdir(parents=True, exist_ok=True)
        client.garth.dump(str(td))
        return {
            "authenticated": True,
            "token_dir": str(td),
        }
    except Exception as e:
        error_msg = str(e)
        # Clean up the error for the user
        if "401" in error_msg:
            error_msg = "Authentication failed: invalid email or password, or MFA required."
        return {
            "authenticated": False,
            "error": error_msg,
        }


def run_auth_flow(token_dir: str, port: int = AUTH_PORT, timeout: int = AUTH_TIMEOUT) -> dict:
    """Run the full browser auth flow. Blocks until auth completes or times out."""
    global _auth_result, _server_ref
    _auth_result = None

    AuthHandler.token_dir = token_dir

    try:
        server = HTTPServer(("127.0.0.1", port), AuthHandler)
    except OSError as e:
        return {"authenticated": False, "error": f"Could not start auth server on port {port}: {e}"}

    _server_ref = server
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://localhost:{port}/auth"
    webbrowser.open(url)

    # Wait for auth result or timeout
    deadline = time.time() + timeout
    while _auth_result is None and time.time() < deadline:
        time.sleep(0.5)

    server.shutdown()
    _server_ref = None

    if _auth_result is None:
        return {
            "authenticated": False,
            "error": f"Timed out after {timeout}s waiting for authentication.",
            "hint": "Open http://localhost:{port}/auth in your browser and sign in.",
        }

    if _auth_result.get("authenticated"):
        return {
            "authenticated": True,
            "message": "Garmin connected. Tokens cached locally. You can now use pull_garmin.",
        }
    else:
        return {
            "authenticated": False,
            "error": _auth_result.get("error", "Unknown error"),
            "hint": "Check your email/password. If you have MFA enabled, ensure you complete it.",
        }
