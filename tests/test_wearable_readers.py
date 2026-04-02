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
            "calories_total, calories_active, calories_bmr, zone2_min, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"g{i}", "p1", d, "garmin", 48.0, 62.0, 9500, 7.5,
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
