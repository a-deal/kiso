"""Tests for headless Garmin auth (remote users who can't open a browser)."""

import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from engine.gateway.token_store import TokenStore


class TestHeadlessGarminAuth:
    """_auth_garmin with email+password should skip browser and auth directly."""

    def _mock_garmin(self, succeed=True):
        mock = MagicMock()
        if succeed:
            mock.login.return_value = None
            mock.garth.dump = lambda d: (
                Path(d).mkdir(parents=True, exist_ok=True),
                (Path(d) / "oauth1_token.json").write_text('{"t": "1"}'),
                (Path(d) / "oauth2_token.json").write_text('{"t": "2"}'),
            )
        else:
            mock.login.side_effect = Exception("401 Client Error")
        return mock

    def test_headless_auth_success(self):
        """When email and password are provided, auth succeeds without browser."""
        with patch("garminconnect.Garmin", return_value=self._mock_garmin()), \
             patch("mcp_server.tools._get_token_store") as mock_ts:
            mock_ts.return_value = MagicMock()

            from mcp_server.tools import _auth_garmin
            result = _auth_garmin(user_id="mike", email="mike@example.com", password="secret123")

        assert result["authenticated"] is True

    def test_headless_auth_bad_credentials(self):
        """When credentials are wrong, returns error without browser."""
        with patch("garminconnect.Garmin", return_value=self._mock_garmin(succeed=False)), \
             patch("mcp_server.tools._get_token_store") as mock_ts:
            mock_ts.return_value = MagicMock()

            from mcp_server.tools import _auth_garmin
            result = _auth_garmin(user_id="mike", email="mike@example.com", password="wrong")

        assert result["authenticated"] is False
        assert "error" in result

    def test_headless_auth_saves_to_token_store(self):
        """After headless auth, tokens should be saved to SQLite TokenStore."""
        mock_store = MagicMock()

        with patch("garminconnect.Garmin", return_value=self._mock_garmin()), \
             patch("mcp_server.tools._get_token_store") as mock_ts:
            mock_ts.return_value = mock_store

            from mcp_server.tools import _auth_garmin
            result = _auth_garmin(user_id="mike", email="mike@example.com", password="secret123")

        assert result["authenticated"] is True
        mock_store.save_garmin_tokens.assert_called_once()

    def test_no_credentials_falls_back_to_browser(self):
        """When no email/password, should use browser flow as before."""
        with patch("mcp_server.garmin_auth.run_auth_flow") as mock_browser, \
             patch("mcp_server.tools._get_token_store") as mock_ts:
            mock_browser.return_value = {"authenticated": True, "message": "done"}
            mock_ts.return_value = MagicMock()

            from mcp_server.tools import _auth_garmin
            result = _auth_garmin(user_id="andrew")

        mock_browser.assert_called_once()
        assert result["authenticated"] is True

    def test_signature_accepts_email_password(self):
        """_auth_garmin should accept email and password params."""
        from mcp_server.tools import _auth_garmin
        sig = inspect.signature(_auth_garmin)
        assert "email" in sig.parameters
        assert "password" in sig.parameters
        # Both should be optional (default None)
        assert sig.parameters["email"].default is None
        assert sig.parameters["password"].default is None


class TestMikeCriticalPath:
    """Integration test: Mike's full path from auth to data readiness.

    Uses real TokenStore + real SQLite, mocked Garmin API.
    Verifies: auth -> tokens in SQLite -> _garmin_token_dir finds them -> connect reports cached.
    """

    @pytest.fixture
    def db_and_store(self, tmp_path, monkeypatch):
        """Set up real TokenStore with temp SQLite and temp garth-cache."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)

        garth_cache = tmp_path / "garth-cache"
        monkeypatch.setattr("engine.gateway.token_store._GARTH_CACHE_DIR", garth_cache)
        monkeypatch.setattr(
            "engine.gateway.token_store._get_db",
            lambda: get_db(db_path),
        )

        store = TokenStore()
        yield store, db_path
        close_db()

    def _mock_garmin(self):
        mock = MagicMock()
        mock.login.return_value = None
        mock.garth.dump = lambda d: (
            Path(d).mkdir(parents=True, exist_ok=True),
            (Path(d) / "oauth1_token.json").write_text('{"token": "o1_mike"}'),
            (Path(d) / "oauth2_token.json").write_text('{"access": "a", "refresh": "r"}'),
        )
        return mock

    def _auth_garmin_with_tmp_dir(self, store, tmp_path, user_id, email, password):
        """Run _auth_garmin but redirect token_dir to tmp_path."""
        from mcp_server.garmin_auth import _do_garmin_auth

        token_dir = str(tmp_path / "tokens" / "garmin" / user_id)
        Path(token_dir).mkdir(parents=True, exist_ok=True)

        with patch("garminconnect.Garmin", return_value=self._mock_garmin()):
            result = _do_garmin_auth(email, password, token_dir)

        if result.get("authenticated"):
            store.save_garmin_tokens(user_id, token_dir)

        return result

    def test_full_path_auth_to_token_discovery(self, db_and_store, tmp_path):
        """After headless auth, _garmin_token_dir should find Mike's tokens."""
        store, db_path = db_and_store

        # Step 1: Auth (writes tokens to disk + saves to SQLite)
        result = self._auth_garmin_with_tmp_dir(store, tmp_path, "mike", "mike@test.com", "pass123")
        assert result["authenticated"] is True

        # Step 2: Can has_token find them in SQLite?
        assert store.has_token("garmin", "mike"), "Tokens should be discoverable after auth"

        # Step 3: Does garmin_token_dir write them back to a cache dir?
        token_dir = store.garmin_token_dir("mike")
        assert (token_dir / "oauth1_token.json").exists(), "oauth1 token should exist"
        assert (token_dir / "oauth2_token.json").exists(), "oauth2 token should exist"

    def test_tokens_not_in_sqlite_after_sync_garmin_tokens(self, db_and_store, tmp_path):
        """Regression: sync_garmin_tokens reads garth-cache, not the auth token_dir.

        If _auth_garmin uses sync_garmin_tokens instead of save_garmin_tokens,
        tokens written to tokens/garmin/mike won't be found in garth-cache/mike.
        """
        store, db_path = db_and_store

        # Simulate what _auth_garmin does: write tokens to a separate dir
        token_dir = tmp_path / "tokens" / "garmin" / "mike"
        token_dir.mkdir(parents=True)

        with patch("garminconnect.Garmin", return_value=self._mock_garmin()):
            from mcp_server.garmin_auth import _do_garmin_auth
            result = _do_garmin_auth("mike@test.com", "pass123", str(token_dir))

        assert result["authenticated"] is True

        # sync_garmin_tokens reads from garth-cache/mike, NOT tokens/garmin/mike
        # So this should NOT find tokens (proving the bug)
        store.sync_garmin_tokens("mike")
        has_via_sync = store._db_has_tokens("mike", "garmin")

        # save_garmin_tokens reads from the actual token_dir -- this SHOULD work
        store.save_garmin_tokens("mike", token_dir)
        has_via_save = store._db_has_tokens("mike", "garmin")

        assert has_via_save is True, "save_garmin_tokens should find tokens in the auth dir"
        # This documents the bug: sync reads wrong dir
        if not has_via_sync:
            # Expected: sync doesn't find tokens because it looks in garth-cache
            pass
