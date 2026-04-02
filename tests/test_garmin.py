"""Tests for Garmin integration (unit tests, no API calls)."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from engine.integrations.garmin import GarminClient, DEFAULT_EXERCISE_MAP


def test_default_exercise_map():
    """Default exercise map should contain common lifts."""
    assert "barbell deadlift" in DEFAULT_EXERCISE_MAP
    assert "barbell bench press" in DEFAULT_EXERCISE_MAP
    assert "barbell back squat" in DEFAULT_EXERCISE_MAP


def test_normalize_exercise_mapped():
    """Known exercises should map to normalized names."""
    client = GarminClient()
    assert client.normalize_exercise("Barbell Deadlift") == "deadlift"
    assert client.normalize_exercise("dumbbell bench press") == "bench_press"
    assert client.normalize_exercise("Back Squat") == "squat"


def test_normalize_exercise_unknown():
    """Unknown exercises should be lowercased and underscored."""
    client = GarminClient()
    assert client.normalize_exercise("Lat Pulldown") == "lat_pulldown"
    assert client.normalize_exercise("Seated Row") == "seated_row"


def test_custom_exercise_map():
    """Custom exercise map should override defaults."""
    custom_map = {"cable fly": "chest_fly", "hammer curl": "bicep_curl"}
    client = GarminClient(exercise_map=custom_map)
    assert client.normalize_exercise("Cable Fly") == "chest_fly"
    assert client.normalize_exercise("Hammer Curl") == "bicep_curl"
    # Unknown exercises still get normalized
    assert client.normalize_exercise("Deadlift") == "deadlift"  # not in custom map


def test_from_config():
    """GarminClient.from_config should parse config dict."""
    config = {
        "garmin": {
            "email": "test@example.com",
            "token_dir": "/tmp/tokens",
        },
        "exercise_name_map": {"front squat": "squat"},
        "data_dir": "/tmp/data",
    }
    client = GarminClient.from_config(config)
    assert client.email == "test@example.com"
    assert str(client.token_dir) == "/tmp/tokens"
    assert client.exercise_map == {"front squat": "squat"}
    assert str(client.data_dir) == "/tmp/data"


def test_has_tokens_no_dir(tmp_path):
    """has_tokens returns False when token dir doesn't exist."""
    assert GarminClient.has_tokens(token_dir=str(tmp_path / "nonexistent")) is False


def test_has_tokens_with_dir(tmp_path):
    """has_tokens returns True when token dir has files."""
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "oauth1_token.json").write_text("{}")
    assert GarminClient.has_tokens(token_dir=str(token_dir)) is True


def test_has_tokens_empty_dir(tmp_path):
    """has_tokens returns False when token dir exists but is empty."""
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    assert GarminClient.has_tokens(token_dir=str(token_dir)) is False


def test_deprecation_warning(capsys):
    """from_config prints deprecation warning when credentials are in config."""
    config = {
        "garmin": {
            "email": "test@example.com",
            "password": "secret",
            "token_dir": "/tmp/tokens",
        },
    }
    GarminClient.from_config(config)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()


# --- Schema tests ---

class TestWearableDailySchema:
    """Verify wearable_daily schema supports zone2_min and multi-source."""

    def test_zone2_min_column_exists(self, tmp_path):
        """wearable_daily should have a zone2_min column."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        cols = {row[1] for row in db.execute("PRAGMA table_info(wearable_daily)").fetchall()}
        assert "zone2_min" in cols, f"zone2_min not in wearable_daily columns: {cols}"
        close_db()

    def test_multi_source_unique_index(self, tmp_path):
        """Two rows with same (person_id, date) but different source should coexist."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO person (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("p1", "Test", now, now),
        )
        db.execute(
            "INSERT INTO wearable_daily (id, person_id, date, source, rhr, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("w1", "p1", "2026-04-02", "garmin", 48.0, now, now),
        )
        db.execute(
            "INSERT INTO wearable_daily (id, person_id, date, source, rhr, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("w2", "p1", "2026-04-02", "apple_health", 50.0, now, now),
        )
        db.commit()
        rows = db.execute(
            "SELECT * FROM wearable_daily WHERE person_id = 'p1' AND date = '2026-04-02'"
        ).fetchall()
        assert len(rows) == 2, f"Expected 2 rows (garmin + apple_health), got {len(rows)}"
        sources = {r["source"] for r in rows}
        assert sources == {"garmin", "apple_health"}
        close_db()


# --- Garmin SQLite write tests ---

class TestGarminSqliteWrite:
    """Verify _append_to_daily_series writes vo2_max and zone2_min to wearable_daily."""

    def _make_client(self, tmp_path):
        data_dir = tmp_path / "data" / "users" / "andrew"
        data_dir.mkdir(parents=True)
        return GarminClient(data_dir=str(data_dir))

    def _setup_db(self, tmp_path):
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p-andrew", "Andrew", "andrew", now, now),
        )
        db.commit()
        return db_path

    def test_append_writes_vo2_max_to_sqlite(self, tmp_path):
        """When snapshot includes vo2_max, it should land in wearable_daily."""
        db_path = self._setup_db(tmp_path)
        from engine.gateway.db import get_db

        client = self._make_client(tmp_path)
        snapshot = {
            "date": "2026-04-02",
            "rhr": 48.0,
            "hrv": 62.0,
            "steps": 9500,
            "sleep_hrs": 7.5,
            "vo2_max": 51.3,
            "deep_sleep_hrs": None, "light_sleep_hrs": None,
            "rem_sleep_hrs": None, "awake_hrs": None,
            "sleep_start": None, "sleep_end": None,
            "hrv_weekly_avg": None, "hrv_status": None,
            "calories_total": None, "calories_active": None,
            "calories_bmr": None, "stress_avg": None,
            "floors": None, "distance_m": None,
            "max_hr": None, "min_hr": None,
        }
        with patch("engine.gateway.db._db_path", return_value=db_path):
            client._append_to_daily_series(snapshot, person_id="p-andrew")

        db = get_db(db_path)
        row = db.execute(
            "SELECT vo2_max FROM wearable_daily WHERE person_id = 'p-andrew' AND date = '2026-04-02'"
        ).fetchone()
        assert row is not None
        assert row["vo2_max"] == 51.3

    def test_append_writes_zone2_min_to_sqlite(self, tmp_path):
        """When snapshot includes zone2_min, it should land in wearable_daily."""
        db_path = self._setup_db(tmp_path)
        from engine.gateway.db import get_db

        client = self._make_client(tmp_path)
        snapshot = {
            "date": "2026-04-02",
            "rhr": 48.0, "hrv": 62.0, "steps": 9500,
            "sleep_hrs": 7.5, "vo2_max": 51.3, "zone2_min": 145,
            "deep_sleep_hrs": None, "light_sleep_hrs": None,
            "rem_sleep_hrs": None, "awake_hrs": None,
            "sleep_start": None, "sleep_end": None,
            "hrv_weekly_avg": None, "hrv_status": None,
            "calories_total": None, "calories_active": None,
            "calories_bmr": None, "stress_avg": None,
            "floors": None, "distance_m": None,
            "max_hr": None, "min_hr": None,
        }
        with patch("engine.gateway.db._db_path", return_value=db_path):
            client._append_to_daily_series(snapshot, person_id="p-andrew")

        db = get_db(db_path)
        row = db.execute(
            "SELECT zone2_min FROM wearable_daily WHERE person_id = 'p-andrew' AND date = '2026-04-02'"
        ).fetchone()
        assert row is not None
        assert row["zone2_min"] == 145
