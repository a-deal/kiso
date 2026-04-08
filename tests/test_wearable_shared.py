"""Shared wearable provider tests — parametrized across Oura and WHOOP.

Tests here cover patterns that are structurally identical between providers:
token storage, schema compat, wearable fallback priority, auth flows,
token refresh, tool registry, and SQLite dual-write.

Provider-specific metric extraction tests stay in test_oura.py / test_whoop.py.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from engine.integrations.oura import OuraClient
from engine.integrations.oura_auth import (
    _exchange_code as oura_exchange_code,
    run_gateway_auth_flow as oura_gateway_auth,
)
from engine.integrations.whoop import WhoopClient
from engine.integrations.whoop_auth import (
    _exchange_code as whoop_exchange_code,
    run_gateway_auth_flow as whoop_gateway_auth,
)

EXCHANGE_FNS = {"oura": oura_exchange_code, "whoop": whoop_exchange_code}
GATEWAY_AUTH_FNS = {"oura": oura_gateway_auth, "whoop": whoop_gateway_auth}
AUTH_MODULES = {"oura": "engine.integrations.oura_auth", "whoop": "engine.integrations.whoop_auth"}


# =====================================================================
# Provider fixtures
# =====================================================================


def _oura_pull_mocks(client, *, empty=False):
    """Return context managers that mock all OuraClient pull methods."""
    if empty:
        return {
            "pull_sleep": patch.object(client, "pull_sleep", return_value=[]),
            "pull_sleep_periods": patch.object(client, "pull_sleep_periods", return_value=[]),
            "pull_activity": patch.object(client, "pull_activity", return_value=[]),
            "pull_readiness": patch.object(client, "pull_readiness", return_value=[]),
        }
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return {
        "pull_sleep": patch.object(client, "pull_sleep", return_value=[
            {"day": today, "total_sleep_duration": 25200},
            {"day": yesterday, "total_sleep_duration": 21600},
        ]),
        "pull_sleep_periods": patch.object(client, "pull_sleep_periods", return_value=[
            {"day": today, "type": "long_sleep", "average_hrv": 45.0,
             "lowest_heart_rate": 62, "bedtime_start": "2026-04-01T23:00:00+00:00",
             "total_sleep_duration": 25200},
            {"day": yesterday, "type": "long_sleep", "average_hrv": 42.0,
             "lowest_heart_rate": 64, "bedtime_start": "2026-03-31T23:30:00+00:00",
             "total_sleep_duration": 21600},
        ]),
        "pull_activity": patch.object(client, "pull_activity", return_value=[
            {"day": today, "steps": 8500},
            {"day": yesterday, "steps": 7200},
        ]),
        "pull_readiness": patch.object(client, "pull_readiness", return_value=[]),
    }


def _whoop_pull_mocks(client, *, empty=False):
    """Return context managers that mock all WhoopClient pull methods."""
    if empty:
        return {
            "pull_recovery": patch.object(client, "pull_recovery", return_value=[]),
            "pull_sleep": patch.object(client, "pull_sleep", return_value=[]),
            "pull_workouts": patch.object(client, "pull_workouts", return_value=[]),
        }
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return {
        "pull_recovery": patch.object(client, "pull_recovery", return_value=[
            {"created_at": f"{today}T08:00:00+00:00",
             "score": {"resting_heart_rate": 58.0, "hrv_rmssd_milli": 72.0}},
            {"created_at": f"{yesterday}T08:00:00+00:00",
             "score": {"resting_heart_rate": 60.0, "hrv_rmssd_milli": 68.0}},
        ]),
        "pull_sleep": patch.object(client, "pull_sleep", return_value=[
            {"start": f"{yesterday}T23:00:00+00:00", "end": f"{today}T06:30:00+00:00",
             "nap": False, "score": {"stage_summary": {"total_in_bed_time_milli": 27000000}}},
        ]),
        "pull_workouts": patch.object(client, "pull_workouts", return_value=[]),
    }


PROVIDERS = [
    pytest.param("oura", OuraClient, _oura_pull_mocks, id="oura"),
    pytest.param("whoop", WhoopClient, _whoop_pull_mocks, id="whoop"),
]


# =====================================================================
# Token storage (previously duplicated across both test files)
# =====================================================================


class TestTokenStorage:
    @pytest.mark.parametrize("service", ["oura", "whoop"])
    def test_save_and_load(self, tmp_path, monkeypatch, service):
        from engine.gateway.token_store import TokenStore
        from engine.gateway.db import init_db, close_db, get_db
        close_db()
        db_path = tmp_path / "test.db"
        init_db(db_path)
        monkeypatch.setattr("engine.gateway.token_store._get_db", lambda: get_db(db_path))

        store = TokenStore(base_dir=tmp_path)
        store._fernet = None

        token_data = {
            "access_token": f"{service}_test_token",
            "refresh_token": f"{service}_refresh",
            "client_id": "test_client",
            "client_secret": "test_secret",
            "scopes": ["daily", "sleep"],
        }

        store.save_token(service, "testuser", token_data)
        loaded = store.load_token(service, "testuser")
        assert loaded == token_data
        close_db()

    @pytest.mark.parametrize("service", ["oura", "whoop"])
    def test_has_token(self, tmp_path, monkeypatch, service):
        from engine.gateway.token_store import TokenStore
        from engine.gateway.db import init_db, close_db, get_db
        close_db()
        db_path = tmp_path / "test.db"
        init_db(db_path)
        monkeypatch.setattr("engine.gateway.token_store._get_db", lambda: get_db(db_path))

        store = TokenStore(base_dir=tmp_path)
        store._fernet = None

        assert not store.has_token(service, "paul")
        store.save_token(service, "paul", {"access_token": "t"})
        assert store.has_token(service, "paul")
        close_db()


# =====================================================================
# Schema compatibility (same REQUIRED_KEYS for both providers)
# =====================================================================


class TestSchemaCompat:
    """Verify pull_all returns the standard wearable schema for all providers."""

    REQUIRED_KEYS = [
        "last_updated", "resting_hr", "daily_steps_avg",
        "sleep_regularity_stddev", "sleep_duration_avg",
        "vo2_max", "hrv_rmssd_avg", "zone2_min_per_week",
    ]

    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_pull_all_returns_all_keys(self, tmp_path, service, ClientClass, mock_fn):
        client = ClientClass(data_dir=str(tmp_path))
        mocks = mock_fn(client, empty=True)
        with _apply_mocks(mocks):
            result = client.pull_all()

        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"



# =====================================================================
# Auth flow (exchange_code + gateway_auth_flow)
# =====================================================================


class TestAuthFlow:
    @pytest.mark.parametrize("service", ["oura", "whoop"])
    def test_exchange_code_error_handling(self, service):
        auth_mod = AUTH_MODULES[service]
        exchange_fn = EXCHANGE_FNS[service]

        with patch(f"{auth_mod}.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = Exception("Connection refused")
            result = exchange_fn("code", "client_id", "secret", "http://localhost/callback")
            assert result.get("error") == "network_error"

    @pytest.mark.parametrize("service", ["oura", "whoop"])
    def test_gateway_auth_flow_saves_tokens(self, tmp_path, service):
        from engine.gateway.token_store import TokenStore
        auth_mod = AUTH_MODULES[service]
        gateway_fn = GATEWAY_AUTH_FNS[service]

        store = TokenStore(base_dir=tmp_path)
        store._fernet = None

        mock_response = json.dumps({
            "access_token": "test_access",
            "refresh_token": "test_refresh",
            "token_type": "Bearer",
            "expires_in": 86400,
        }).encode()

        with patch(f"{auth_mod}.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = gateway_fn(
                code="test_code",
                client_id="cid",
                client_secret="csecret",
                redirect_uri="http://localhost/callback",
                user_id="paul",
                token_store=store,
            )

        assert result["authenticated"] is True
        assert result["user_id"] == "paul"

        saved = store.load_token(service, "paul")
        assert saved is not None
        assert saved["access_token"] == "test_access"
        assert saved["refresh_token"] == "test_refresh"
        assert saved["client_id"] == "cid"
        assert saved["client_secret"] == "csecret"


# =====================================================================
# Token refresh
# =====================================================================


class TestTokenRefresh:
    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_refresh_updates_token(self, tmp_path, service, ClientClass, mock_fn):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        store._fernet = None

        store.save_token(service, "default", {
            "access_token": "old_token",
            "refresh_token": "refresh_123",
            "client_id": "cid",
            "client_secret": "csecret",
        })

        client = ClientClass(user_id="default", token_store=store)

        mock_response = json.dumps({
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_in": 86400,
        }).encode()

        with patch(f"engine.integrations.{service}.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = client._refresh_token()

        assert result is True
        assert client._access_token == "new_token"

        saved = store.load_token(service, "default")
        assert saved["access_token"] == "new_token"
        assert saved["refresh_token"] == "new_refresh"

    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_refresh_fails_no_refresh_token(self, tmp_path, service, ClientClass, mock_fn):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        store._fernet = None

        store.save_token(service, "default", {
            "access_token": "old_token",
            "client_id": "cid",
            "client_secret": "csecret",
        })

        client = ClientClass(user_id="default", token_store=store)
        assert client._refresh_token() is False


# =====================================================================
# Tool registry
# =====================================================================


class TestToolRegistry:
    @pytest.mark.parametrize("service", ["oura", "whoop"])
    def test_connect_wearable_supports_provider(self, service):
        from mcp_server.tools import _connect_wearable
        result = _connect_wearable(service, user_id="nonexistent_test_user")
        assert "error" not in result or "Unsupported" not in result.get("error", "")


# =====================================================================
# Wearable fallback priority (was split across both files)
# =====================================================================


class TestWearableFallback:
    """_load_wearable_data priority: garmin > oura > whoop > apple_health."""

    def test_garmin_takes_priority(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "garmin_latest.json").write_text(json.dumps({"resting_hr": 55, "source": "garmin"}))
        (tmp_path / "oura_latest.json").write_text(json.dumps({"resting_hr": 60, "source": "oura"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 55

    def test_oura_fallback(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "oura_latest.json").write_text(json.dumps({"resting_hr": 60, "source": "oura"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 60

    def test_oura_before_whoop(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "oura_latest.json").write_text(json.dumps({"resting_hr": 60, "source": "oura"}))
        (tmp_path / "whoop_latest.json").write_text(json.dumps({"resting_hr": 57, "source": "whoop"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 60

    def test_whoop_fallback(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "whoop_latest.json").write_text(json.dumps({"resting_hr": 57, "source": "whoop"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 57

    def test_garmin_over_whoop(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "garmin_latest.json").write_text(json.dumps({"resting_hr": 55, "source": "garmin"}))
        (tmp_path / "whoop_latest.json").write_text(json.dumps({"resting_hr": 57, "source": "whoop"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 55

    def test_oura_before_apple(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "oura_latest.json").write_text(json.dumps({"resting_hr": 60, "source": "oura"}))
        (tmp_path / "apple_health_latest.json").write_text(json.dumps({"resting_hr": 62, "source": "apple"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 60

    def test_whoop_before_apple(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        (tmp_path / "whoop_latest.json").write_text(json.dumps({"resting_hr": 57, "source": "whoop"}))
        (tmp_path / "apple_health_latest.json").write_text(json.dumps({"resting_hr": 62, "source": "apple"}))
        result = _load_wearable_data(tmp_path)
        assert result["resting_hr"] == 57

    def test_no_wearable(self, tmp_path):
        from mcp_server.tools import _load_wearable_data
        result = _load_wearable_data(tmp_path)
        assert result is None


# =====================================================================
# SQLite dual-write
# =====================================================================


class TestSqliteDualWrite:
    """Verify pull_all writes daily series to wearable_daily for all providers."""

    def _setup_db(self, tmp_path):
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p-grigoriy", "Grigoriy", "grigoriy", now, now),
        )
        db.commit()
        return db_path

    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_pull_all_writes_to_wearable_daily(self, tmp_path, service, ClientClass, mock_fn):
        db_path = self._setup_db(tmp_path)
        client = ClientClass(user_id="grigoriy", data_dir=str(tmp_path / "data"))
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)

        mocks = mock_fn(client, empty=False)
        with patch("engine.gateway.db._db_path", return_value=db_path), _apply_mocks(mocks):
            client.pull_all(history=True, history_days=2, person_id="p-grigoriy")

        from engine.gateway.db import get_db
        db = get_db(db_path)
        rows = db.execute(
            "SELECT date, source, rhr, hrv, sleep_hrs FROM wearable_daily "
            "WHERE person_id = 'p-grigoriy' ORDER BY date"
        ).fetchall()

        assert len(rows) >= 1
        sources = {r["source"] for r in rows}
        assert service in sources

    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_pull_all_without_person_id_skips_sqlite(self, tmp_path, service, ClientClass, mock_fn):
        client = ClientClass(user_id="test", data_dir=str(tmp_path / "data"))
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)

        mocks = mock_fn(client, empty=True)
        with _apply_mocks(mocks):
            result = client.pull_all()

        assert result is not None


# =====================================================================
# pull_all return value tests
# =====================================================================


class TestPullAllNoData:
    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_no_data_doesnt_overwrite(self, tmp_path, service, ClientClass, mock_fn):
        out_path = tmp_path / f"{service}_latest.json"
        out_path.write_text('{"existing": true}')

        client = ClientClass(data_dir=str(tmp_path))
        mocks = mock_fn(client, empty=True)
        with _apply_mocks(mocks):
            result = client.pull_all()

        assert result["resting_hr"] is None
        assert result["hrv_rmssd_avg"] is None
        assert json.loads(out_path.read_text()) == {"existing": True}

    @pytest.mark.parametrize("service,ClientClass,mock_fn", PROVIDERS)
    def test_history_tier4_no_json_written(self, tmp_path, service, ClientClass, mock_fn):
        """Tier 4: JSON files should NOT be written."""
        client = ClientClass(data_dir=str(tmp_path))
        mocks = mock_fn(client, empty=True)
        with _apply_mocks(mocks):
            client.pull_all(history=True, history_days=5)

        assert not (tmp_path / f"{service}_daily.json").exists()


# =====================================================================
# Helpers
# =====================================================================


class _apply_mocks:
    """Context manager that enters all patch objects in a dict."""

    def __init__(self, mocks: dict):
        self._mocks = mocks
        self._active = []

    def __enter__(self):
        for m in self._mocks.values():
            self._active.append(m.__enter__())
        return self

    def __exit__(self, *exc):
        for m in self._mocks.values():
            m.__exit__(*exc)
        return False
