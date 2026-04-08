"""Tests for WHOOP integration — provider-specific metric extraction.

Shared tests (token storage, schema compat, auth, fallback, SQLite write)
live in test_wearable_shared.py.
"""

import statistics
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from engine.integrations.whoop import WhoopClient


# =====================================================================
# WhoopClient unit tests
# =====================================================================


class TestWhoopHasTokens:
    def test_no_tokens(self, tmp_path):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        assert WhoopClient.has_tokens(user_id="nobody", token_store=store) is False

    def test_with_tokens(self, tmp_path):
        from engine.gateway.token_store import TokenStore
        store = TokenStore(base_dir=tmp_path)
        store._fernet = None
        store.save_token("whoop", "testuser", {"access_token": "test123"})
        assert WhoopClient.has_tokens(user_id="testuser", token_store=store) is True


# =====================================================================
# Metric extraction tests
# =====================================================================


class TestExtractRHR:
    def test_from_recovery(self):
        client = WhoopClient()
        recovery = [
            {"score": {"resting_heart_rate": 58}},
            {"score": {"resting_heart_rate": 60}},
            {"score": {"resting_heart_rate": 56}},
        ]
        result = client._extract_resting_hr(recovery)
        assert result == round(statistics.mean([58, 60, 56]), 1)

    def test_empty_recovery(self):
        client = WhoopClient()
        result = client._extract_resting_hr([])
        assert result is None

    def test_skips_zero(self):
        client = WhoopClient()
        recovery = [
            {"score": {"resting_heart_rate": 0}},
            {"score": {"resting_heart_rate": 55}},
        ]
        result = client._extract_resting_hr(recovery)
        assert result == 55.0


class TestExtractHRV:
    def test_from_recovery(self):
        client = WhoopClient()
        recovery = [
            {"score": {"hrv_rmssd_milli": 45.0}},
            {"score": {"hrv_rmssd_milli": 50.0}},
            {"score": {"hrv_rmssd_milli": 55.0}},
        ]
        result = client._extract_hrv(recovery)
        assert result == 50.0

    def test_empty_recovery(self):
        client = WhoopClient()
        result = client._extract_hrv([])
        assert result is None

    def test_skips_zero(self):
        client = WhoopClient()
        recovery = [
            {"score": {"hrv_rmssd_milli": 0}},
            {"score": {"hrv_rmssd_milli": 50.0}},
        ]
        result = client._extract_hrv(recovery)
        assert result == 50.0


class TestExtractSleepDuration:
    def test_basic(self):
        client = WhoopClient()
        sleep_data = [
            {"score": {"stage_summary": {"total_in_bed_time_milli": 7 * 3600 * 1000}}},
            {"score": {"stage_summary": {"total_in_bed_time_milli": 8 * 3600 * 1000}}},
        ]
        result = client._extract_sleep_duration(sleep_data)
        assert result == 7.5

    def test_empty(self):
        client = WhoopClient()
        result = client._extract_sleep_duration([])
        assert result is None

    def test_rejects_unreasonable(self):
        client = WhoopClient()
        sleep_data = [
            {"score": {"stage_summary": {"total_in_bed_time_milli": 100}}},
        ]
        result = client._extract_sleep_duration(sleep_data)
        assert result is None


class TestExtractSleepRegularity:
    def test_consistent_bedtime(self):
        client = WhoopClient()
        sleep_data = [
            {"start": "2026-03-20T22:30:00-07:00", "nap": False},
            {"start": "2026-03-21T22:35:00-07:00", "nap": False},
            {"start": "2026-03-22T22:25:00-07:00", "nap": False},
        ]
        result = client._extract_sleep_regularity(sleep_data)
        assert result is not None
        assert result < 10

    def test_irregular_bedtime(self):
        client = WhoopClient()
        sleep_data = [
            {"start": "2026-03-20T21:00:00-07:00", "nap": False},
            {"start": "2026-03-21T01:00:00-07:00", "nap": False},
            {"start": "2026-03-22T23:00:00-07:00", "nap": False},
        ]
        result = client._extract_sleep_regularity(sleep_data)
        assert result is not None
        assert result > 30

    def test_skips_naps(self):
        client = WhoopClient()
        sleep_data = [
            {"start": "2026-03-20T22:30:00-07:00", "nap": False},
            {"start": "2026-03-20T14:00:00-07:00", "nap": True},
            {"start": "2026-03-21T22:30:00-07:00", "nap": False},
        ]
        result = client._extract_sleep_regularity(sleep_data)
        assert result is not None
        assert result < 5

    def test_insufficient_data(self):
        client = WhoopClient()
        sleep_data = [
            {"start": "2026-03-20T22:30:00-07:00", "nap": False},
        ]
        result = client._extract_sleep_regularity(sleep_data)
        assert result is None


class TestExtractZone2:
    def test_basic(self):
        client = WhoopClient()
        today = date.today()
        workouts = [
            {
                "start": (datetime.combine(today - timedelta(days=1), datetime.min.time())).isoformat() + "Z",
                "score": {"zone_durations": {"zone_two_milli": 30 * 60 * 1000}},
            },
            {
                "start": (datetime.combine(today - timedelta(days=2), datetime.min.time())).isoformat() + "Z",
                "score": {"zone_durations": {"zone_two_milli": 20 * 60 * 1000}},
            },
        ]
        result = client._extract_zone2_from_workouts(workouts, days=7)
        assert result == 50

    def test_excludes_old_data(self):
        client = WhoopClient()
        today = date.today()
        workouts = [
            {
                "start": (datetime.combine(today - timedelta(days=30), datetime.min.time())).isoformat() + "Z",
                "score": {"zone_durations": {"zone_two_milli": 60 * 60 * 1000}},
            },
        ]
        result = client._extract_zone2_from_workouts(workouts, days=7)
        assert result is None

    def test_no_zone_data(self):
        client = WhoopClient()
        result = client._extract_zone2_from_workouts([], days=7)
        assert result is None


# =====================================================================
# pull_all tests (provider-specific assertions)
# =====================================================================


class TestDailySeriesSchema:
    def test_daily_series_schema(self):
        client = WhoopClient()
        series = client._build_daily_series([], [], days=3)
        assert len(series) == 3
        for entry in series:
            for key in ("date", "rhr", "hrv", "steps", "sleep_hrs", "sleep_start", "sleep_end"):
                assert key in entry


class TestPullAll:
    def test_saves_whoop_latest(self, tmp_path):
        """pull_all should return correct WHOOP-specific values."""
        client = WhoopClient(data_dir=str(tmp_path))

        recovery = [
            {"score": {"resting_heart_rate": 58, "hrv_rmssd_milli": 50.0}},
        ]
        sleep = [
            {
                "start": "2026-03-20T22:30:00-07:00",
                "end": "2026-03-21T06:30:00-07:00",
                "nap": False,
                "score": {"stage_summary": {"total_in_bed_time_milli": 8 * 3600 * 1000}},
            },
            {
                "start": "2026-03-21T22:35:00-07:00",
                "end": "2026-03-22T06:35:00-07:00",
                "nap": False,
                "score": {"stage_summary": {"total_in_bed_time_milli": 8 * 3600 * 1000}},
            },
        ]

        with patch.object(client, 'pull_recovery', return_value=recovery), \
             patch.object(client, 'pull_sleep', return_value=sleep), \
             patch.object(client, 'pull_workouts', return_value=[]):
            result = client.pull_all()

        # WHOOP-specific: no steps, no VO2 max
        assert result["daily_steps_avg"] is None
        assert result["vo2_max"] is None
        assert result["resting_hr"] == 58.0
        assert result["hrv_rmssd_avg"] == 50.0
        assert result["sleep_duration_avg"] == 8.0
        assert result["source"] == "whoop"

    def test_pull_all_sqlite_values(self, tmp_path):
        """Verify WHOOP-specific metric-to-column mapping in wearable_daily."""
        from engine.gateway.db import init_db, get_db, close_db
        close_db()
        db_path = tmp_path / "kasane.db"
        init_db(db_path)
        db = get_db(db_path)
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p-test", "Test", "test", now, now),
        )
        db.commit()

        client = WhoopClient(user_id="test", data_dir=str(tmp_path / "data"))
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with patch("engine.gateway.db._db_path", return_value=db_path), \
             patch.object(client, "pull_recovery", return_value=[
                 {"created_at": f"{today}T08:00:00+00:00",
                  "score": {"resting_heart_rate": 58.0, "hrv_rmssd_milli": 72.0}},
             ]), \
             patch.object(client, "pull_sleep", return_value=[
                 {"start": f"{yesterday}T23:00:00+00:00", "end": f"{today}T06:30:00+00:00",
                  "nap": False, "score": {"stage_summary": {"total_in_bed_time_milli": 27000000}}},
             ]), \
             patch.object(client, "pull_workouts", return_value=[]):
            client.pull_all(history=True, history_days=2, person_id="p-test")

        row = db.execute(
            "SELECT source, rhr, hrv, sleep_hrs FROM wearable_daily "
            "WHERE person_id = 'p-test' AND date = ?", (today,)
        ).fetchone()
        assert row is not None
        assert row["source"] == "whoop"
        assert row["rhr"] == 58.0
        assert row["hrv"] == 72.0
        assert row["sleep_hrs"] == 7.5
        close_db()

    def test_saves_daily_series_with_history(self, tmp_path):
        """pull_all with history=True should build daily series."""
        client = WhoopClient(data_dir=str(tmp_path))

        today = date.today()
        recovery = [
            {
                "created_at": f"{(today - timedelta(days=i)).isoformat()}T08:00:00Z",
                "score": {"resting_heart_rate": 55, "hrv_rmssd_milli": 50.0},
            }
            for i in range(5)
        ]
        sleep = [
            {
                "start": f"{(today - timedelta(days=i+1)).isoformat()}T22:30:00-07:00",
                "end": f"{(today - timedelta(days=i)).isoformat()}T06:30:00-07:00",
                "nap": False,
                "score": {"stage_summary": {"total_in_bed_time_milli": 8 * 3600 * 1000}},
            }
            for i in range(5)
        ]

        with patch.object(client, 'pull_recovery', return_value=recovery), \
             patch.object(client, 'pull_sleep', return_value=sleep), \
             patch.object(client, 'pull_workouts', return_value=[]):
            client.pull_all(history=True, history_days=5)


# =====================================================================
# Pagination tests (WHOOP-specific)
# =====================================================================


class TestPagination:
    def test_single_page(self):
        client = WhoopClient()
        client._token_data = {"access_token": "test"}
        client._access_token = "test"

        page1 = {"records": [{"id": 1}, {"id": 2}]}

        with patch.object(client, '_api_get', return_value=page1):
            result = client._api_get_all("recovery")

        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_multi_page(self):
        client = WhoopClient()
        client._token_data = {"access_token": "test"}
        client._access_token = "test"

        page1 = {"records": [{"id": 1}], "next_token": "abc123"}
        page2 = {"records": [{"id": 2}]}

        call_count = 0

        def mock_api_get(endpoint, params=None, retry_on_401=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return page1
            return page2

        with patch.object(client, '_api_get', side_effect=mock_api_get):
            result = client._api_get_all("recovery")

        assert len(result) == 2
        assert call_count == 2

    def test_empty_response(self):
        client = WhoopClient()
        client._token_data = {"access_token": "test"}
        client._access_token = "test"

        with patch.object(client, '_api_get', return_value=None):
            result = client._api_get_all("recovery")

        assert result == []
