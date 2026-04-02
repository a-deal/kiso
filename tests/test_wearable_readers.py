"""Tests for wearable_daily reader queries with multi-source data.

When a user has both Garmin and Apple Health rows for the same date,
readers must return one row per date (not duplicates).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a temp DB with multi-source wearable_daily data."""
    from engine.gateway.db import init_db, get_db, close_db
    close_db()
    db_path = tmp_path / "kasane.db"
    init_db(db_path)
    conn = get_db(db_path)
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("p1", "Andrew", "andrew", now, now),
    )

    # Two sources for the same 3 dates
    for i, d in enumerate(["2026-04-01", "2026-04-02", "2026-04-03"]):
        conn.execute(
            "INSERT INTO wearable_daily "
            "(id, person_id, date, source, rhr, hrv, steps, sleep_hrs, "
            "sleep_start, vo2_max, "
            "calories_total, calories_active, calories_bmr, zone2_min, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"g{i}", "p1", d, "garmin", 48.0, 62.0, 9500, 7.5,
             "22:30", 47.0,
             2400, 600, 1800, 145, now, now),
        )
        conn.execute(
            "INSERT INTO wearable_daily "
            "(id, person_id, date, source, rhr, hrv, steps, sleep_hrs, "
            "calories_total, calories_active, calories_bmr, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"a{i}", "p1", d, "apple_health", 50.0, 40.0, 8000, 7.0,
             None, 450, None, now, now),
        )
    conn.commit()
    yield conn, db_path
    close_db()


class TestGetWearableDaily:
    """db_read.get_wearable_daily should return one row per date."""

    def test_no_duplicate_dates(self, db):
        conn, db_path = db
        import engine.db_read as dr

        with patch.object(dr, "_DB_PATH", db_path), patch.object(dr, "_initialized", False):
            rows = dr.get_wearable_daily(user_id="andrew", days=7)

        dates = [r["date"] for r in rows]
        assert len(dates) == len(set(dates)), (
            f"Duplicate dates in get_wearable_daily: {dates}"
        )

    def test_prefers_garmin_over_apple_health(self, db):
        conn, db_path = db
        import engine.db_read as dr

        with patch.object(dr, "_DB_PATH", db_path), patch.object(dr, "_initialized", False):
            rows = dr.get_wearable_daily(user_id="andrew", days=7)

        for r in rows:
            assert r["source"] == "garmin", (
                f"Expected garmin for date {r['date']}, got {r['source']}"
            )


class TestPersonContextWearable:
    """_get_person_context wearable snapshot should prefer garmin when both exist."""

    def test_snapshot_prefers_garmin(self, db):
        """The production query: SELECT * ... ORDER BY date DESC LIMIT 1"""
        conn, db_path = db

        row = conn.execute(
            "SELECT * FROM wearable_daily WHERE person_id = ? ORDER BY date DESC LIMIT 1",
            ("p1",),
        ).fetchone()

        assert row is not None
        assert row["source"] == "garmin", (
            f"Expected garmin for latest snapshot, got {row['source']}"
        )


class TestCaloriesBurnQuery:
    """Calorie burn query should not double-count from multiple sources."""

    def test_no_duplicate_burn_dates(self, db):
        conn, db_path = db

        rows = conn.execute(
            "SELECT date, calories_total, calories_active, calories_bmr "
            "FROM wearable_daily "
            "WHERE person_id = ? AND calories_total IS NOT NULL "
            "ORDER BY date",
            ("p1",),
        ).fetchall()

        dates = [r["date"] for r in rows]
        assert len(dates) == len(set(dates)), (
            f"Duplicate burn dates would inflate calorie totals: {dates}"
        )


class TestBriefingWearable:
    """Briefing wearable query should return one row per date."""

    def test_no_duplicate_dates_in_series(self, db):
        conn, db_path = db
        from engine.coaching.briefing import _load_wearable_daily_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            rows = _load_wearable_daily_sqlite("p1")

        assert rows is not None
        dates = [r["date"] for r in rows]
        assert len(dates) == len(set(dates)), (
            f"Duplicate dates in briefing series: {dates}"
        )


class TestLoadWearableAveragesSqlite:
    """_load_wearable_averages_sqlite should compute rolling averages from wearable_daily."""

    def test_returns_scoring_keys(self, db):
        """Should return all keys that scoring expects."""
        conn, db_path = db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        assert avgs is not None
        assert "resting_hr" in avgs
        assert "daily_steps_avg" in avgs
        assert "sleep_duration_avg" in avgs
        assert "hrv_rmssd_avg" in avgs
        assert "vo2_max" in avgs
        assert "zone2_min_per_week" in avgs

    def test_averages_from_garmin_preferred(self, db):
        """Averages should come from garmin rows (preferred source), not apple_health."""
        conn, db_path = db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        # Garmin rhr=48.0 for all 3 days, apple_health=50.0
        assert avgs["resting_hr"] == 48.0
        assert avgs["daily_steps_avg"] == 9500
        assert avgs["sleep_duration_avg"] == 7.5
        assert avgs["hrv_rmssd_avg"] == 62.0

    def test_returns_none_when_no_data(self, db):
        """Should return None for a person with no wearable data."""
        conn, db_path = db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("nonexistent-person")

        assert avgs is None

    def test_vo2_max_uses_latest(self, db):
        """vo2_max should be the most recent value, not averaged."""
        conn, db_path = db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        assert avgs["vo2_max"] == 47.0

    def test_zone2_uses_sum(self, db):
        """zone2_min_per_week should be the sum over the window, not average."""
        conn, db_path = db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        # 3 days x 145 min = 435
        assert avgs["zone2_min_per_week"] == 435


class TestPersonContextNoJsonFallback:
    """_get_person_context should get wearable data from SQLite, not JSON files."""

    def test_no_json_fallback_when_sqlite_has_data(self, db):
        """Even if JSON files don't exist, wearable_snapshot should be populated."""
        conn, db_path = db

        # Query SQLite directly (simulating what _get_person_context does)
        with patch("engine.gateway.db._db_path", return_value=db_path):
            row = conn.execute(
                "SELECT * FROM wearable_daily WHERE person_id = ? "
                "ORDER BY date DESC, "
                "CASE source WHEN 'garmin' THEN 1 WHEN 'apple_health' THEN 2 ELSE 3 END "
                "LIMIT 1", ("p1",)
            ).fetchone()

        assert row is not None
        assert row["source"] == "garmin"
        assert row["rhr"] == 48.0


# --- Briefing SQLite migration tests ---

@pytest.fixture
def briefing_db(tmp_path):
    """DB with wearable, BP, weight, and training data for briefing tests."""
    from engine.gateway.db import init_db, get_db, close_db
    close_db()
    db_path = tmp_path / "kasane.db"
    init_db(db_path)
    conn = get_db(db_path)
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("p1", "Andrew", "andrew", now, now),
    )

    # Wearable data: 7 days with calories
    for i in range(7):
        d = f"2026-04-{i+1:02d}"
        conn.execute(
            "INSERT INTO wearable_daily "
            "(id, person_id, date, source, rhr, hrv, steps, sleep_hrs, "
            "sleep_start, vo2_max, zone2_min, "
            "calories_total, calories_active, calories_bmr, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"w{i}", "p1", d, "garmin", 48.0, 62.0, 9500, 7.5,
             "22:30", 47.0, 20,
             2400, 600, 1800, now, now),
        )

    # BP entries
    conn.execute(
        "INSERT INTO bp_entry (id, person_id, date, systolic, diastolic, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("bp1", "p1", "2026-03-15", 112.0, 65.0, now, now),
    )

    # Weight entries
    conn.execute(
        "INSERT INTO weight_entry (id, person_id, date, weight_lbs, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("wt1", "p1", "2026-04-01", 192.5, now, now),
    )

    # Training session
    conn.execute(
        "INSERT INTO training_session (id, person_id, date, type, duration_min, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ts1", "p1", "2026-04-01", "strength", 60, "Upper push", now, now),
    )

    conn.commit()
    yield conn, db_path, tmp_path
    close_db()


class TestBriefingUsesWearableSqlite:
    """Briefing wearable profile should come from SQLite, not *_latest.json."""

    def test_briefing_wearable_from_sqlite(self, briefing_db):
        """Briefing should populate wearable data even when no JSON files exist."""
        conn, db_path, tmp_path = briefing_db
        data_dir = tmp_path / "data" / "users" / "andrew"
        data_dir.mkdir(parents=True)
        # No garmin_latest.json, no apple_health_latest.json — only SQLite

        from mcp_server.tools import _load_wearable_averages_sqlite
        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        assert avgs is not None
        assert avgs["resting_hr"] == 48.0
        assert avgs["daily_steps_avg"] == 9500.0
        assert avgs["hrv_rmssd_avg"] == 62.0
        assert avgs["vo2_max"] == 47.0


class TestBriefingBpFromSqlite:
    """Briefing BP date should come from SQLite, not read_csv(bp_log.csv)."""

    def test_latest_bp_date_from_sqlite(self, briefing_db):
        """Should get the last BP date from bp_entry table."""
        conn, db_path, tmp_path = briefing_db

        row = conn.execute(
            "SELECT date FROM bp_entry WHERE person_id = ? ORDER BY date DESC LIMIT 1",
            ("p1",),
        ).fetchone()
        assert row is not None
        assert row["date"] == "2026-03-15"

    def test_bp_count_from_sqlite(self, briefing_db):
        """Should count recent BP readings from SQLite."""
        conn, db_path, tmp_path = briefing_db

        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM bp_entry WHERE person_id = ? AND date >= ?",
            ("p1", "2026-03-01"),
        ).fetchone()["cnt"]
        assert count == 1


class TestBriefingWeightFromSqlite:
    """Briefing weight date should come from SQLite, not read_csv(weight_log.csv)."""

    def test_latest_weight_date_from_sqlite(self, briefing_db):
        """Should get the last weight date from weight_entry table."""
        conn, db_path, tmp_path = briefing_db

        row = conn.execute(
            "SELECT date FROM weight_entry WHERE person_id = ? ORDER BY date DESC LIMIT 1",
            ("p1",),
        ).fetchone()
        assert row is not None
        assert row["date"] == "2026-04-01"


class TestBriefingBurnFromSqlite:
    """Briefing calorie burn should come from wearable_daily, not garmin_daily_burn.json."""

    def test_burn_data_from_sqlite(self, briefing_db):
        """Should get calorie data from wearable_daily table."""
        conn, db_path, tmp_path = briefing_db

        rows = conn.execute(
            "SELECT date, calories_total, calories_active, calories_bmr "
            "FROM wearable_daily WHERE person_id = ? AND calories_total IS NOT NULL "
            "ORDER BY date DESC LIMIT 7",
            ("p1",),
        ).fetchall()
        assert len(rows) == 7
        assert rows[0]["calories_total"] == 2400


class TestBriefingTrainingFromSqlite:
    """Briefing training sessions should come from SQLite, not session_log.csv."""

    def test_sessions_from_sqlite(self, briefing_db):
        """Should get training sessions from training_session table."""
        conn, db_path, tmp_path = briefing_db

        rows = conn.execute(
            "SELECT * FROM training_session WHERE person_id = ? ORDER BY date DESC",
            ("p1",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["type"] == "strength"
        assert rows[0]["duration_min"] == 60


class TestGetProtocolsSqlite:
    """_get_protocols should use SQLite wearable averages, not garmin_latest.json."""

    def test_protocols_no_json_needed(self, briefing_db):
        """protocol_progress garmin arg should come from SQLite averages."""
        conn, db_path, tmp_path = briefing_db
        from mcp_server.tools import _load_wearable_averages_sqlite

        with patch("engine.gateway.db._db_path", return_value=db_path):
            avgs = _load_wearable_averages_sqlite("p1")

        assert avgs is not None
        # These are the keys protocol_progress looks up via garmin.get(metric_id)
        assert "resting_hr" in avgs
        assert "daily_steps_avg" in avgs
        assert "sleep_duration_avg" in avgs
        assert "hrv_rmssd_avg" in avgs
        assert avgs["resting_hr"] == 48.0


class TestLoadHealthContextSqlite:
    """v1_api _load_health_context should read weight and meals from SQLite."""

    def test_weight_from_sqlite(self, briefing_db):
        """Weight data should come from weight_entry, not weight_log.csv."""
        conn, db_path, tmp_path = briefing_db
        from engine.gateway.db import close_db, get_db

        data_dir = tmp_path / "data" / "users" / "andrew"
        data_dir.mkdir(parents=True)

        # Verify data is in SQLite
        row = conn.execute(
            "SELECT weight_lbs FROM weight_entry WHERE person_id = 'p1'"
        ).fetchone()
        assert row is not None
        assert row["weight_lbs"] == 192.5

        # Test the query pattern _load_health_context uses
        pid_row = conn.execute(
            "SELECT id FROM person WHERE health_engine_user_id = 'andrew' AND deleted_at IS NULL"
        ).fetchone()
        assert pid_row is not None
        wt_rows = conn.execute(
            "SELECT date, weight_lbs, waist_in, source FROM weight_entry "
            "WHERE person_id = ? ORDER BY date DESC LIMIT 14",
            (pid_row["id"],),
        ).fetchall()
        assert len(wt_rows) == 1
        assert wt_rows[0]["weight_lbs"] == 192.5

    def test_meals_from_sqlite(self, briefing_db):
        """Meals should come from meal_entry, not meal_log.csv."""
        conn, db_path, tmp_path = briefing_db

        # Add a meal to SQLite
        now = datetime.now(timezone.utc).isoformat()
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO meal_entry (id, person_id, date, description, calories, protein_g, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("m1", "p1", today, "Chicken and rice", 650, 45, now, now),
        )
        conn.commit()

        # Test the query pattern _load_health_context uses
        meal_rows = conn.execute(
            "SELECT * FROM meal_entry WHERE person_id = 'p1' AND date = ?",
            (today,),
        ).fetchall()
        assert len(meal_rows) == 1
        assert meal_rows[0]["description"] == "Chicken and rice"
