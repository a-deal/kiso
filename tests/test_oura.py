"""Tests for Oura Ring integration — provider-specific metric extraction.

Shared tests (token storage, schema compat, auth, fallback, SQLite write)
live in test_wearable_shared.py.
"""

import statistics
from datetime import date, timedelta
from unittest.mock import patch

from engine.integrations.oura import OuraClient


# =====================================================================
# OuraClient unit tests
# =====================================================================


class TestOuraHasTokens:
    def test_no_tokens(self, tmp_path):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        assert OuraClient.has_tokens(user_id="nobody", token_store=store) is False

    def test_with_tokens(self, tmp_path):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        store._fernet = None
        store.save_token("oura", "testuser", {"access_token": "test123"})
        assert OuraClient.has_tokens(user_id="testuser", token_store=store) is True


class TestExtractRHR:
    def test_from_sleep_periods_via_extract_resting_hr(self):
        """_extract_resting_hr now delegates to sleep periods (not readiness
        contributor scores, which are 1-100 scores, not bpm)."""
        client = OuraClient()
        sleep_periods = [
            {"lowest_heart_rate": 52},
            {"lowest_heart_rate": 54},
            {"lowest_heart_rate": 56},
        ]
        result = client._extract_resting_hr([], sleep_periods)
        assert result == round(statistics.mean([52, 54, 56]), 1)

    def test_empty_data(self):
        client = OuraClient()
        result = client._extract_resting_hr([], [])
        assert result is None

    def test_from_sleep_periods(self):
        client = OuraClient()
        periods = [
            {"lowest_heart_rate": 52},
            {"lowest_heart_rate": 54},
        ]
        result = client._extract_resting_hr_from_sleep_periods(periods)
        assert result == 53.0


class TestExtractHRV:
    def test_from_sleep_periods(self):
        client = OuraClient()
        periods = [
            {"average_hrv": 45.0},
            {"average_hrv": 50.0},
            {"average_hrv": 55.0},
        ]
        result = client._extract_hrv(periods)
        assert result == 50.0

    def test_empty_periods(self):
        client = OuraClient()
        result = client._extract_hrv([])
        assert result is None

    def test_skips_zero(self):
        client = OuraClient()
        periods = [
            {"average_hrv": 0},
            {"average_hrv": 50.0},
        ]
        result = client._extract_hrv(periods)
        assert result == 50.0


class TestExtractSleepDuration:
    def test_basic(self):
        client = OuraClient()
        daily_sleep = [
            {"total_sleep_duration": 7 * 3600},
            {"total_sleep_duration": 8 * 3600},
        ]
        result = client._extract_sleep_duration(daily_sleep)
        assert result == 7.5

    def test_empty(self):
        client = OuraClient()
        result = client._extract_sleep_duration([])
        assert result is None


class TestExtractSleepRegularity:
    def test_consistent_bedtime(self):
        client = OuraClient()
        periods = [
            {"type": "long_sleep", "bedtime_start": "2026-03-20T22:30:00-07:00"},
            {"type": "long_sleep", "bedtime_start": "2026-03-21T22:35:00-07:00"},
            {"type": "long_sleep", "bedtime_start": "2026-03-22T22:25:00-07:00"},
        ]
        result = client._extract_sleep_regularity(periods)
        assert result is not None
        assert result < 10

    def test_irregular_bedtime(self):
        client = OuraClient()
        periods = [
            {"type": "long_sleep", "bedtime_start": "2026-03-20T21:00:00-07:00"},
            {"type": "long_sleep", "bedtime_start": "2026-03-21T01:00:00-07:00"},
            {"type": "long_sleep", "bedtime_start": "2026-03-22T23:00:00-07:00"},
        ]
        result = client._extract_sleep_regularity(periods)
        assert result is not None
        assert result > 30

    def test_skips_naps(self):
        client = OuraClient()
        periods = [
            {"type": "long_sleep", "bedtime_start": "2026-03-20T22:30:00-07:00"},
            {"type": "rest", "bedtime_start": "2026-03-20T14:00:00-07:00"},
            {"type": "long_sleep", "bedtime_start": "2026-03-21T22:30:00-07:00"},
        ]
        result = client._extract_sleep_regularity(periods)
        assert result is not None
        assert result < 5

    def test_insufficient_data(self):
        client = OuraClient()
        periods = [
            {"type": "long_sleep", "bedtime_start": "2026-03-20T22:30:00-07:00"},
        ]
        result = client._extract_sleep_regularity(periods)
        assert result is None


class TestExtractSteps:
    def test_basic(self):
        client = OuraClient()
        activity = [
            {"steps": 8000},
            {"steps": 10000},
            {"steps": 12000},
        ]
        result = client._extract_steps(activity)
        assert result == 10000

    def test_empty(self):
        client = OuraClient()
        result = client._extract_steps([])
        assert result is None


class TestExtractZone2:
    def test_basic(self):
        client = OuraClient()
        today = date.today()
        activity = [
            {"day": (today - timedelta(days=1)).isoformat(), "medium_activity_met_minutes": 80},
            {"day": (today - timedelta(days=2)).isoformat(), "medium_activity_met_minutes": 60},
        ]
        result = client._extract_zone2_minutes(activity, days=7)
        assert result is not None
        assert result > 0

    def test_excludes_old_data(self):
        client = OuraClient()
        today = date.today()
        activity = [
            {"day": (today - timedelta(days=30)).isoformat(), "medium_activity_met_minutes": 200},
        ]
        result = client._extract_zone2_minutes(activity, days=7)
        assert result is None


class TestDailySeriesSchema:
    def test_daily_series_schema(self):
        client = OuraClient()
        series = client._build_daily_series([], [], [], days=3)
        assert len(series) == 3
        for entry in series:
            for key in ("date", "rhr", "hrv", "steps", "sleep_hrs", "sleep_start", "sleep_end"):
                assert key in entry


class TestPullAll:
    def test_saves_oura_latest(self, tmp_path):
        """pull_all should return correct values from mocked API data."""
        client = OuraClient(data_dir=str(tmp_path))

        with patch.object(client, 'pull_sleep', return_value=[
            {"day": "2026-03-20", "total_sleep_duration": 7 * 3600},
        ]), patch.object(client, 'pull_sleep_periods', return_value=[
            {"day": "2026-03-20", "type": "long_sleep", "average_hrv": 50.0, "lowest_heart_rate": 55,
             "bedtime_start": "2026-03-20T22:30:00-07:00", "total_sleep_duration": 7 * 3600},
        ]), patch.object(client, 'pull_activity', return_value=[
            {"day": "2026-03-20", "steps": 9500},
        ]), patch.object(client, 'pull_readiness', return_value=[]):
            result = client.pull_all()

        assert result["vo2_max"] is None
        assert result["resting_hr"] == 55.0
        assert result["hrv_rmssd_avg"] == 50.0
        assert result["sleep_duration_avg"] == 7.0
        assert result["daily_steps_avg"] == 9500

    def test_pull_all_sqlite_values(self, tmp_path):
        """Verify Oura-specific metric-to-column mapping in wearable_daily."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        from datetime import datetime
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p-test", "Test", "test", now, now),
        )
        db.commit()

        client = OuraClient(user_id="test", data_dir=str(tmp_path / "data"))
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        with patch("engine.gateway.db._db_path", return_value=db_path), \
             patch.object(client, "pull_sleep", return_value=[
                 {"day": today, "total_sleep_duration": 25200},
             ]), \
             patch.object(client, "pull_sleep_periods", return_value=[
                 {"day": today, "type": "long_sleep", "average_hrv": 45.0,
                  "lowest_heart_rate": 62, "bedtime_start": "2026-04-01T23:00:00+00:00",
                  "total_sleep_duration": 25200},
             ]), \
             patch.object(client, "pull_activity", return_value=[
                 {"day": today, "steps": 8500},
             ]), \
             patch.object(client, "pull_readiness", return_value=[]):
            client.pull_all(history=True, history_days=2, person_id="p-test")

        row = db.execute(
            "SELECT source, rhr, hrv, steps, sleep_hrs FROM wearable_daily "
            "WHERE person_id = 'p-test' AND date = ?", (today,)
        ).fetchone()
        assert row is not None
        assert row["source"] == "oura"
        assert row["rhr"] == 62.0
        assert row["hrv"] == 45.0
        assert row["steps"] == 8500
        assert row["sleep_hrs"] == 7.0
        close_db()

    def test_saves_daily_series_with_history(self, tmp_path):
        """pull_all with history=True should build daily series."""
        client = OuraClient(data_dir=str(tmp_path))

        today = date.today()
        sleep_data = [
            {"day": (today - timedelta(days=i)).isoformat(), "total_sleep_duration": 7 * 3600}
            for i in range(5)
        ]
        period_data = [
            {"day": (today - timedelta(days=i)).isoformat(), "type": "long_sleep",
             "average_hrv": 50.0, "lowest_heart_rate": 55,
             "bedtime_start": f"{(today - timedelta(days=i)).isoformat()}T22:30:00-07:00",
             "total_sleep_duration": 7 * 3600}
            for i in range(5)
        ]
        activity_data = [
            {"day": (today - timedelta(days=i)).isoformat(), "steps": 9000}
            for i in range(5)
        ]

        with patch.object(client, 'pull_sleep', return_value=sleep_data), \
             patch.object(client, 'pull_sleep_periods', return_value=period_data), \
             patch.object(client, 'pull_activity', return_value=activity_data), \
             patch.object(client, 'pull_readiness', return_value=[]):
            client.pull_all(history=True, history_days=5)
