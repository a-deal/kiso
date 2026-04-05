"""Tests for Apple Health ingest endpoint (Baseline Sync app)."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _recent_ts(hours_ago: int = 0) -> str:
    """Generate an ISO timestamp within the 48-hour freshness window."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat()

from engine.gateway.config import GatewayConfig
from engine.gateway.server import create_app
from mcp_server.tools import _ingest_health_snapshot, TOOL_REGISTRY


# --- Tool function tests ---

class TestIngestHealthSnapshot:

    @pytest.fixture
    def data_dir(self, tmp_path):
        d = tmp_path / "data" / "users" / "test_user"
        d.mkdir(parents=True)
        return d

    def test_registered_in_tool_registry(self):
        assert "ingest_health_snapshot" in TOOL_REGISTRY
        assert TOOL_REGISTRY["ingest_health_snapshot"] is _ingest_health_snapshot

    def test_full_snapshot(self, data_dir):
        metrics = {
            "resting_hr": 54.2,
            "hrv_sdnn": 42.5,
            "steps": 8450,
            "sleep_hours": 7.1,
            "sleep_start": "22:45",
            "sleep_end": "06:10",
            "weight_lbs": 192.5,
            "vo2_max": 51.3,
            "blood_oxygen": 97.2,
            "active_calories": 450,
            "respiratory_rate": 14.2,
        }
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(
                user_id="test_user",
                metrics=metrics,
                timestamp=_recent_ts(1),
            )

        assert result["ingested"] is True
        assert result["metrics_count"] == 11
        assert result["series_length"] == 1
        assert result["latest_updated"] is True
        assert result["weight_logged"] is True

        # Check daily series file
        daily = json.loads((data_dir / "apple_health_daily.json").read_text())
        assert len(daily) == 1
        assert daily[0]["resting_hr"] == 54.2
        assert daily[0]["hrv_method"] == "SDNN"

        # Check latest file
        latest = json.loads((data_dir / "apple_health_latest.json").read_text())
        assert latest["source"] == "apple_health"
        assert latest["resting_hr"] == 54.2
        assert latest["hrv_rmssd_avg"] == 29.7  # SDNN 42.5 * 0.7 conversion factor
        assert latest["metadata"]["hrv_method"] == "SDNN"
        assert latest["metadata"]["hrv_sdnn_to_rmssd_factor"] == 0.7

    def test_partial_metrics(self, data_dir):
        """Only some fields present. Should ingest what's there."""
        metrics = {
            "resting_hr": 58.0,
            "steps": 6000,
        }
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(user_id="test_user", metrics=metrics)

        assert result["ingested"] is True
        assert result["metrics_count"] == 2
        assert "resting_hr" in result["metrics_stored"]
        assert "steps" in result["metrics_stored"]

        latest = json.loads((data_dir / "apple_health_latest.json").read_text())
        assert latest["resting_hr"] == 58.0
        assert latest["daily_steps_avg"] == 6000.0
        assert latest["hrv_rmssd_avg"] is None  # Not provided

    def test_appends_to_series(self, data_dir):
        """Multiple snapshots should append, not overwrite."""
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 55.0, "steps": 7000},
                timestamp=_recent_ts(24),
            )
            result = _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 53.0, "steps": 9000},
                timestamp=_recent_ts(1),
            )

        assert result["series_length"] == 2

        daily = json.loads((data_dir / "apple_health_daily.json").read_text())
        assert len(daily) == 2
        assert daily[0]["resting_hr"] == 55.0
        assert daily[1]["resting_hr"] == 53.0

    def test_rolling_averages(self, data_dir):
        """Latest file should compute rolling averages from recent entries."""
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 60.0},
                timestamp=_recent_ts(24),
            )
            _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 50.0},
                timestamp=_recent_ts(1),
            )

        latest = json.loads((data_dir / "apple_health_latest.json").read_text())
        assert latest["resting_hr"] == 55.0  # (60 + 50) / 2

    def test_invalid_token_rejection(self):
        """API should reject invalid tokens."""
        config = GatewayConfig(port=18899, api_token="test-token-123")
        app = create_app(config)
        client = TestClient(app)

        response = client.post(
            "/api/ingest_health_snapshot",
            json={
                "token": "wrong-token",
                "user_id": "paul",
                "metrics": {"resting_hr": 54.0},
            },
        )
        assert response.status_code == 403

    def test_missing_user_id(self, data_dir):
        """Should reject when user_id is empty."""
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(user_id="", metrics={"resting_hr": 54.0})
        assert result["ingested"] is False
        assert "user_id" in result["error"]

    def test_empty_metrics(self, data_dir):
        """Should reject when metrics dict is empty."""
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(user_id="test_user", metrics={})
        assert result["ingested"] is False

    def test_unknown_keys_ignored(self, data_dir):
        """Unknown metric keys should be noted but not cause failure."""
        metrics = {
            "resting_hr": 54.0,
            "made_up_metric": 999,
        }
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(user_id="test_user", metrics=metrics)

        assert result["ingested"] is True
        assert result["metrics_count"] == 1
        assert "made_up_metric" in result["unknown_keys_ignored"]

    def test_all_unknown_keys(self, data_dir):
        """If all keys are unknown, should return error."""
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(
                user_id="test_user",
                metrics={"bogus": 1, "fake": 2},
            )
        assert result["ingested"] is False
        assert "No valid metrics" in result["error"]

    def test_none_values_skipped(self, data_dir):
        """None values in metrics should be filtered out."""
        metrics = {
            "resting_hr": 54.0,
            "hrv_sdnn": None,
            "steps": None,
        }
        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            result = _ingest_health_snapshot(user_id="test_user", metrics=metrics)

        assert result["ingested"] is True
        assert result["metrics_count"] == 1
        assert result["metrics_stored"] == ["resting_hr"]


# --- HTTP endpoint tests ---

class TestIngestEndpoint:

    @pytest.fixture
    def client(self):
        config = GatewayConfig(port=18899, api_token="test-token-123")
        app = create_app(config)
        return TestClient(app)

    def test_post_with_token_in_body(self, client, tmp_path):
        """Token can be sent in JSON body, not just query param."""
        data_dir = tmp_path / "data" / "users" / "paul"
        data_dir.mkdir(parents=True)

        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            response = client.post(
                "/api/ingest_health_snapshot",
                json={
                    "token": "test-token-123",
                    "user_id": "paul",
                    "metrics": {
                        "resting_hr": 54.2,
                        "steps": 8450,
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ingested"] is True
        assert data["metrics_count"] == 2

    def test_post_with_token_in_query(self, client, tmp_path):
        """Traditional query param token should still work."""
        data_dir = tmp_path / "data" / "users" / "paul"
        data_dir.mkdir(parents=True)

        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            response = client.post(
                "/api/ingest_health_snapshot?token=test-token-123",
                json={
                    "user_id": "paul",
                    "metrics": {"resting_hr": 54.2},
                },
            )

        assert response.status_code == 200
        assert response.json()["ingested"] is True

    def test_post_no_token(self, client):
        """Should reject when no token provided at all."""
        response = client.post(
            "/api/ingest_health_snapshot",
            json={
                "user_id": "paul",
                "metrics": {"resting_hr": 54.2},
            },
        )
        assert response.status_code == 403

    def test_get_with_flat_metric_params(self, client, tmp_path):
        """Simplified flow: flat query params instead of nested metrics dict."""
        data_dir = tmp_path / "data" / "users" / "default"
        data_dir.mkdir(parents=True)

        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            response = client.get(
                "/api/ingest_health_snapshot?token=test-token-123"
                "&resting_hr=58.5&steps=9200&hrv_sdnn=42.3"
                "&weight_lbs=192.5&sleep_start=23:15"
                "&sleep_end=06:45"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ingested"] is True
        assert data["metrics_count"] == 6
        assert set(data["metrics_stored"]) == {
            "resting_hr", "steps", "hrv_sdnn",
            "weight_lbs", "sleep_start", "sleep_end",
        }

    def test_get_flat_params_with_user_id(self, client, tmp_path):
        """Flat params with explicit user_id."""
        data_dir = tmp_path / "data" / "users" / "paul"
        data_dir.mkdir(parents=True)

        with patch("mcp_server.tools._data_dir", return_value=data_dir):
            response = client.get(
                "/api/ingest_health_snapshot?token=test-token-123"
                "&user_id=paul&resting_hr=62&vo2_max=38.5"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ingested"] is True
        assert data["metrics_count"] == 2


# --- SQLite wearable_daily dual-write tests ---

class TestIngestWritesWearableDaily:
    """Verify _ingest_health_snapshot writes to wearable_daily SQLite table."""

    @pytest.fixture
    def setup_db(self, tmp_path):
        """Create a temp SQLite DB with a person row, return (data_dir, db_path, person_id)."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        person_id = "p-test-001"
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (person_id, "Test User", "test_user", now, now),
        )
        db.commit()

        data_dir = tmp_path / "data" / "users" / "test_user"
        data_dir.mkdir(parents=True)

        yield data_dir, db_path, person_id

        close_db()

    def _patch_db(self, setup_db):
        """Return a context manager that patches DB calls to use the test DB."""
        data_dir, db_path, person_id = setup_db
        from engine.gateway.db import get_db, init_db
        from functools import partial
        return (
            data_dir, db_path, person_id,
            patch("mcp_server.tools._data_dir", return_value=data_dir),
            patch("engine.gateway.db.get_db", side_effect=partial(get_db, db_path)),
            patch("engine.gateway.db.init_db", side_effect=partial(init_db, db_path)),
        )

    def test_ingest_writes_to_wearable_daily(self, setup_db):
        """A full ingest should create a row in wearable_daily."""
        data_dir, db_path, person_id = setup_db
        from engine.gateway.db import get_db

        metrics = {
            "resting_hr": 54.2,
            "hrv_sdnn": 42.5,
            "steps": 8450,
            "sleep_hours": 7.1,
            "sleep_start": "22:45",
            "sleep_end": "06:10",
            "vo2_max": 51.3,
            "active_calories": 450,
        }
        with (
            patch("mcp_server.tools._data_dir", return_value=data_dir),
            patch("engine.gateway.db._db_path", return_value=db_path),
        ):
            result = _ingest_health_snapshot(
                user_id="test_user",
                metrics=metrics,
                timestamp=_recent_ts(1),
            )

        assert result["ingested"] is True

        db = get_db(db_path)
        row = db.execute(
            "SELECT * FROM wearable_daily WHERE person_id = ? AND source = 'apple_health'",
            (person_id,),
        ).fetchone()
        assert row is not None, "Expected a wearable_daily row for apple_health source"
        assert row["rhr"] == 54.2
        assert row["hrv"] == 42.5
        assert row["steps"] == 8450
        assert row["sleep_hrs"] == 7.1
        assert row["sleep_start"] == "22:45"
        assert row["sleep_end"] == "06:10"
        assert row["vo2_max"] == 51.3
        assert row["calories_active"] == 450.0

    def test_ingest_upserts_same_day(self, setup_db):
        """Second ingest on same day should update, not duplicate."""
        data_dir, db_path, person_id = setup_db
        from engine.gateway.db import get_db

        with (
            patch("mcp_server.tools._data_dir", return_value=data_dir),
            patch("engine.gateway.db._db_path", return_value=db_path),
        ):
            _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 54.0, "steps": 8000},
                timestamp=_recent_ts(12),
            )
            # Second ingest same day, different values
            _ingest_health_snapshot(
                user_id="test_user",
                metrics={"resting_hr": 52.0, "steps": 9000},
                timestamp=_recent_ts(1),
            )

        db = get_db(db_path)
        rows = db.execute(
            "SELECT * FROM wearable_daily WHERE person_id = ? AND source = 'apple_health'",
            (person_id,),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 row (upsert), got {len(rows)}"
        assert rows[0]["rhr"] == 52.0
        assert rows[0]["steps"] == 9000

    def test_ingest_no_person_no_crash(self, tmp_path):
        """If user has no person row, ingest should still succeed (JSON only)."""
        from engine.gateway.db import init_db, close_db
        db_path = tmp_path / "kasane.db"
        close_db()
        init_db(db_path)

        data_dir = tmp_path / "data" / "users" / "orphan"
        data_dir.mkdir(parents=True)

        with (
            patch("mcp_server.tools._data_dir", return_value=data_dir),
            patch("engine.gateway.db._db_path", return_value=db_path),
        ):
            result = _ingest_health_snapshot(
                user_id="orphan",
                metrics={"resting_hr": 60.0},
                timestamp=_recent_ts(1),
            )

        assert result["ingested"] is True

        close_db()


class TestConnectWearableAppleHealth:
    """Verify connect_wearable returns Baseline Sync instructions, not Shortcuts."""

    def test_returns_baseline_sync_not_shortcuts(self):
        from mcp_server.tools import _connect_wearable

        result = _connect_wearable("apple_health", user_id="test_user")
        assert result["supported"] is True
        assert result["setup_method"] == "ios_app"
        instructions = result["coach_instructions"].lower()
        assert "baseline" in instructions
        assert "shortcut" not in instructions
        assert "icloud.com" not in instructions

    def test_apple_watch_alias(self):
        from mcp_server.tools import _connect_wearable

        result = _connect_wearable("apple_watch", user_id="test_user")
        assert result["supported"] is True
        assert result["setup_method"] == "ios_app"

    def test_apple_alias(self):
        from mcp_server.tools import _connect_wearable

        result = _connect_wearable("apple", user_id="test_user")
        assert result["supported"] is True
        assert result["setup_method"] == "ios_app"
