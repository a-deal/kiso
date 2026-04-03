"""Tests for the behavior change measurement loop.

Covers: hypothesis recording with baseline computation, outcome measurement
after 24h, bulk measurement cron, and read-back for review.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from engine.gateway.db import init_db, get_db


# --- Fixtures ---

_NOW = "2026-04-02T00:00:00Z"


def _insert_person(db, id="andrew-001", name="Andrew", user_id="andrew",
                   tz="America/Los_Angeles"):
    db.execute(
        "INSERT INTO person (id, name, health_engine_user_id, timezone, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (id, name, user_id, tz, _NOW, _NOW),
    )
    db.commit()


def _insert_wearable_days(db, person_id, days: list[dict]):
    """Insert wearable_daily rows. Each dict needs: date, plus metric columns."""
    import uuid
    for d in days:
        rid = str(uuid.uuid4())
        cols = ["id", "person_id", "date", "source", "created_at", "updated_at"]
        vals = [rid, person_id, d["date"], "garmin", _NOW, _NOW]
        for k, v in d.items():
            if k != "date":
                cols.append(k)
                vals.append(v)
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        db.execute(f"INSERT INTO wearable_daily ({col_names}) VALUES ({placeholders})", vals)
    db.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    actual_db_path = tmp_path / "data" / "kasane.db"
    init_db(str(actual_db_path))
    conn = get_db(str(actual_db_path))
    return conn


@pytest.fixture
def db_with_andrew(db):
    _insert_person(db)
    return db


@pytest.fixture
def db_with_baseline(db_with_andrew):
    """Andrew with 7 days of wearable data for baseline computation."""
    days = []
    for i in range(7):
        date = (datetime(2026, 4, 1) - timedelta(days=i)).strftime("%Y-%m-%d")
        days.append({"date": date, "steps": 8000 + i * 100, "sleep_hrs": 7.0 + i * 0.1, "hrv": 55 + i})
    _insert_wearable_days(db_with_andrew, "andrew-001", days)
    return db_with_andrew


# --- Tests: record_hypothesis ---

class TestRecordHypothesis:
    def test_creates_row_with_baseline(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis

        row = record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase next-day step count by 10%",
            metric_key="steps",
        )
        assert row["id"] is not None
        assert row["hypothesis"] == "increase next-day step count by 10%"
        assert row["metric_key"] == "steps"
        assert row["baseline_value"] is not None
        assert row["baseline_value"] > 0
        assert row["measured_at"] is None

    def test_baseline_is_7day_average(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis

        row = record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="test",
            metric_key="steps",
        )
        # 8000, 8100, 8200, 8300, 8400, 8500, 8600 -> avg = 8300
        assert row["baseline_value"] == pytest.approx(8300, abs=1)

    def test_no_wearable_data_baseline_is_none(self, db_with_andrew):
        from engine.coaching.outcomes import record_hypothesis

        row = record_hypothesis(
            db_with_andrew, "andrew-001",
            hypothesis="test",
            metric_key="steps",
        )
        assert row["baseline_value"] is None

    def test_with_scheduled_send_id(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis

        row = record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="test",
            metric_key="hrv",
            scheduled_send_id=42,
        )
        assert row["scheduled_send_id"] == 42

    def test_invalid_metric_key_rejected(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis

        with pytest.raises(ValueError, match="metric_key"):
            record_hypothesis(
                db_with_baseline, "andrew-001",
                hypothesis="test",
                metric_key="nonexistent_column",
            )


# --- Tests: measure_outcomes ---

class TestMeasureOutcomes:
    def test_measures_after_24h(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes

        # Record hypothesis "yesterday"
        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        # Backdate the created_at to 36h ago so it's eligible
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ((datetime(2026, 4, 2) - timedelta(hours=36)).isoformat(),)
        )
        db_with_baseline.commit()

        # Add a fresh day of data (the "after" measurement)
        _insert_wearable_days(db_with_baseline, "andrew-001", [
            {"date": "2026-04-02", "steps": 10000}
        ])

        results = measure_outcomes(db_with_baseline)
        assert len(results) == 1
        assert results[0]["measured_value"] == 10000
        assert results[0]["delta"] == pytest.approx(10000 - 8300, abs=1)
        assert results[0]["measured_at"] is not None

    def test_skips_recently_created(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes

        # Record hypothesis just now - should NOT be measured yet
        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        results = measure_outcomes(db_with_baseline)
        assert len(results) == 0

    def test_skips_already_measured(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        # Backdate and measure once
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ((datetime(2026, 4, 2) - timedelta(hours=36)).isoformat(),)
        )
        db_with_baseline.commit()
        _insert_wearable_days(db_with_baseline, "andrew-001", [
            {"date": "2026-04-02", "steps": 10000}
        ])

        measure_outcomes(db_with_baseline)
        # Second call should find nothing new
        results = measure_outcomes(db_with_baseline)
        assert len(results) == 0

    def test_filters_by_person_id(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ((datetime(2026, 4, 2) - timedelta(hours=36)).isoformat(),)
        )
        db_with_baseline.commit()
        _insert_wearable_days(db_with_baseline, "andrew-001", [
            {"date": "2026-04-02", "steps": 10000}
        ])

        # Filter for a different person - should find nothing
        results = measure_outcomes(db_with_baseline, person_id="nobody")
        assert len(results) == 0

    def test_no_post_data_leaves_unmeasured(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        # Backdate to 36h ago but AFTER all baseline data (latest is 2026-04-01)
        # so no wearable data exists after the hypothesis date
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ("2026-04-01T12:00:00",)
        )
        db_with_baseline.commit()
        # No new wearable data added after 2026-04-01

        results = measure_outcomes(db_with_baseline)
        assert len(results) == 0


# --- Tests: get_outcomes ---

class TestGetOutcomes:
    def test_returns_measured_outcomes(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, measure_outcomes, get_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ((datetime(2026, 4, 2) - timedelta(hours=36)).isoformat(),)
        )
        db_with_baseline.commit()
        _insert_wearable_days(db_with_baseline, "andrew-001", [
            {"date": "2026-04-02", "steps": 10000}
        ])
        measure_outcomes(db_with_baseline)

        outcomes = get_outcomes(db_with_baseline, "andrew-001")
        assert len(outcomes) == 1
        assert outcomes[0]["hypothesis"] == "increase steps"
        assert outcomes[0]["delta"] is not None

    def test_includes_unmeasured_by_default(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, get_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="test",
            metric_key="steps",
        )
        outcomes = get_outcomes(db_with_baseline, "andrew-001")
        assert len(outcomes) == 1
        assert outcomes[0]["measured_at"] is None

    def test_days_filter(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, get_outcomes

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="test",
            metric_key="steps",
        )
        # Backdate to 60 days ago
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = ?",
            ((datetime(2026, 4, 2) - timedelta(days=60)).isoformat(),)
        )
        db_with_baseline.commit()

        outcomes = get_outcomes(db_with_baseline, "andrew-001", days=30)
        assert len(outcomes) == 0

        outcomes = get_outcomes(db_with_baseline, "andrew-001", days=90)
        assert len(outcomes) == 1


# --- Tests: export_outcomes_csv ---

class TestExportOutcomesCsv:
    def test_returns_csv_string_with_header(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, export_outcomes_csv

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps by 10%",
            metric_key="steps",
        )
        csv_str = export_outcomes_csv(db_with_baseline, "andrew-001")
        lines = csv_str.strip().split("\n")
        assert len(lines) == 2  # header + 1 data row
        header = lines[0]
        assert "hypothesis" in header
        assert "metric_key" in header
        assert "baseline_value" in header
        assert "delta" in header

    def test_includes_measured_and_unmeasured(self, db_with_baseline):
        from engine.coaching.outcomes import (
            record_hypothesis, measure_outcomes, export_outcomes_csv,
        )

        # One unmeasured
        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="increase steps",
            metric_key="steps",
        )
        # One that will be measured
        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="improve sleep",
            metric_key="sleep_hrs",
        )
        # Backdate both and add post data
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = '2026-03-30T12:00:00'"
        )
        db_with_baseline.commit()
        _insert_wearable_days(db_with_baseline, "andrew-001", [
            {"date": "2026-04-02", "steps": 10000, "sleep_hrs": 8.0}
        ])
        measure_outcomes(db_with_baseline)

        csv_str = export_outcomes_csv(db_with_baseline, "andrew-001")
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3  # header + 2 data rows

    def test_empty_when_no_outcomes(self, db_with_andrew):
        from engine.coaching.outcomes import export_outcomes_csv

        csv_str = export_outcomes_csv(db_with_andrew, "andrew-001")
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_csv_is_parseable(self, db_with_baseline):
        import csv
        import io
        from engine.coaching.outcomes import record_hypothesis, export_outcomes_csv

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis='hypothesis with, commas and "quotes"',
            metric_key="steps",
        )
        csv_str = export_outcomes_csv(db_with_baseline, "andrew-001")
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["hypothesis"] == 'hypothesis with, commas and "quotes"'
        assert rows[0]["metric_key"] == "steps"

    def test_days_filter_applies(self, db_with_baseline):
        from engine.coaching.outcomes import record_hypothesis, export_outcomes_csv

        record_hypothesis(
            db_with_baseline, "andrew-001",
            hypothesis="old",
            metric_key="steps",
        )
        db_with_baseline.execute(
            "UPDATE coaching_outcome SET created_at = '2025-01-01T00:00:00'"
        )
        db_with_baseline.commit()

        csv_str = export_outcomes_csv(db_with_baseline, "andrew-001", days=30)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1  # header only, old row filtered out


# --- Tests: extract_hypothesis ---

class TestExtractHypothesis:
    """Rule-based extraction of hypothesis + metric_key from coaching messages."""

    def test_hrv_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Your HRV dropped to 52. Try getting to bed by 10:30 tonight.")
        assert result is not None
        assert result["metric_key"] == "hrv"
        assert len(result["hypothesis"]) > 0

    def test_sleep_hours_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Sleep was 5.8 hrs last night. That's dragging recovery.")
        assert result is not None
        assert result["metric_key"] == "sleep_hrs"

    def test_steps_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("You've hit 6k steps today. Try a 15-min walk before dinner.")
        assert result is not None
        assert result["metric_key"] == "steps"

    def test_rhr_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("RHR trending up from 48 to 52 this week.")
        assert result is not None
        assert result["metric_key"] == "rhr"

    def test_resting_heart_rate_phrase(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Your resting heart rate has been climbing. Focus on recovery.")
        assert result is not None
        assert result["metric_key"] == "rhr"

    def test_walk_maps_to_steps(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Try a 20-minute walk after lunch today.")
        assert result is not None
        assert result["metric_key"] == "steps"

    def test_body_battery_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Body battery is at 25. Take it easy today.")
        assert result is not None
        assert result["metric_key"] == "body_battery"

    def test_stress_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Stress levels have been elevated all week. Try 10 min of breathwork.")
        assert result is not None
        assert result["metric_key"] == "stress_avg"

    def test_no_metric_returns_none(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("No meals logged yet. Try to log dinner before bed.")
        assert result is None

    def test_generic_encouragement_returns_none(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Great job staying consistent this week. Keep it up!")
        assert result is None

    def test_dashboard_link_not_matched(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html")
        assert result is None

    def test_first_metric_wins(self):
        from engine.coaching.outcomes import extract_hypothesis
        # Sleep mentioned first, then steps
        result = extract_hypothesis("Sleep was 5.8 hrs. You also only hit 4k steps. Move more tomorrow.")
        assert result is not None
        assert result["metric_key"] == "sleep_hrs"

    def test_case_insensitive(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("your hrv is looking solid at 71ms.")
        assert result is not None
        assert result["metric_key"] == "hrv"

    def test_deep_sleep_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("Deep sleep was only 45 minutes. That's well below your average.")
        assert result is not None
        assert result["metric_key"] == "deep_sleep_hrs"

    def test_zone2_mention(self):
        from engine.coaching.outcomes import extract_hypothesis
        result = extract_hypothesis("You logged 0 zone 2 minutes this week. Even a brisk walk counts.")
        assert result is not None
        assert result["metric_key"] == "zone2_min"


# --- Tests: extract_hypothesis wired into scheduler ---

class TestHypothesisWiring:
    """Scheduler calls extract_hypothesis + record_hypothesis after compose."""

    def test_hypothesis_recorded_on_send(self, db_with_baseline):
        from unittest.mock import patch, MagicMock
        from engine.gateway.scheduler import _run_schedule

        with patch("engine.gateway.scheduler._compose_message", return_value="Your HRV dropped to 52. Try bed by 10:30."), \
             patch("engine.gateway.scheduler._gather_context", return_value={}), \
             patch("engine.gateway.scheduler._user_local_now") as mock_now, \
             patch("engine.gateway.scheduler._get_eligible_persons") as mock_persons, \
             patch("engine.gateway.scheduler._audit_scheduler"):

            mock_persons.return_value = [
                {"id": "andrew-001", "name": "Andrew", "health_engine_user_id": "andrew",
                 "channel": "whatsapp", "channel_target": "+14152009584", "timezone": "America/Los_Angeles"},
            ]
            from zoneinfo import ZoneInfo
            mock_now.return_value = datetime(2026, 4, 2, 7, 10, tzinfo=ZoneInfo("America/Los_Angeles"))

            _run_schedule("morning_brief", target_hour=7, dry_run=True)

        row = db_with_baseline.execute("SELECT * FROM coaching_outcome").fetchone()
        assert row is not None
        # Should have extracted hrv as the metric
        assert db_with_baseline.execute(
            "SELECT metric_key FROM coaching_outcome WHERE person_id = 'andrew-001'"
        ).fetchone()[0] == "hrv"

    def test_no_hypothesis_when_no_metric(self, db_with_baseline):
        from unittest.mock import patch
        from engine.gateway.scheduler import _run_schedule

        with patch("engine.gateway.scheduler._compose_message", return_value="Great job staying consistent!"), \
             patch("engine.gateway.scheduler._gather_context", return_value={}), \
             patch("engine.gateway.scheduler._user_local_now") as mock_now, \
             patch("engine.gateway.scheduler._get_eligible_persons") as mock_persons, \
             patch("engine.gateway.scheduler._audit_scheduler"):

            mock_persons.return_value = [
                {"id": "andrew-001", "name": "Andrew", "health_engine_user_id": "andrew",
                 "channel": "whatsapp", "channel_target": "+14152009584", "timezone": "America/Los_Angeles"},
            ]
            from zoneinfo import ZoneInfo
            mock_now.return_value = datetime(2026, 4, 2, 7, 10, tzinfo=ZoneInfo("America/Los_Angeles"))

            _run_schedule("morning_brief", target_hour=7, dry_run=True)

        count = db_with_baseline.execute("SELECT COUNT(*) FROM coaching_outcome").fetchone()[0]
        assert count == 0


# --- Tests: MCP tool wrappers for conversational hypothesis recording ---

class TestRecordHypothesisTool:
    """MCP tool: record_hypothesis — lets Milo record a hypothesis during conversation."""

    def test_registered_in_tool_registry(self):
        from mcp_server.tools import TOOL_REGISTRY, _record_hypothesis_tool
        assert "record_hypothesis" in TOOL_REGISTRY
        assert TOOL_REGISTRY["record_hypothesis"] is _record_hypothesis_tool

    def test_records_with_baseline(self, db_with_baseline):
        from unittest.mock import patch
        from mcp_server.tools import _record_hypothesis_tool

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_baseline), \
             patch("engine.gateway.db.init_db"):
            result = _record_hypothesis_tool(
                hypothesis="improve sleep duration",
                metric_key="sleep_hrs",
                user_id="andrew",
            )

        assert result["status"] == "recorded"
        assert result["outcome"]["hypothesis"] == "improve sleep duration"
        assert result["outcome"]["metric_key"] == "sleep_hrs"
        assert result["outcome"]["baseline_value"] is not None

    def test_records_without_baseline(self, db_with_andrew):
        """No wearable data means baseline is None, but hypothesis still records."""
        from unittest.mock import patch
        from mcp_server.tools import _record_hypothesis_tool

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_andrew), \
             patch("engine.gateway.db.init_db"):
            result = _record_hypothesis_tool(
                hypothesis="increase daily step count",
                metric_key="steps",
                user_id="andrew",
            )

        assert result["status"] == "recorded"
        assert result["outcome"]["baseline_value"] is None

    def test_rejects_invalid_metric_key(self, db_with_andrew):
        from unittest.mock import patch
        from mcp_server.tools import _record_hypothesis_tool

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_andrew), \
             patch("engine.gateway.db.init_db"):
            result = _record_hypothesis_tool(
                hypothesis="improve vibes",
                metric_key="vibes",
                user_id="andrew",
            )

        assert result["status"] == "error"
        assert "vibes" in result["message"]

    def test_requires_person(self):
        from unittest.mock import patch
        from mcp_server.tools import _record_hypothesis_tool

        with patch("mcp_server.tools._resolve_person_id", return_value=None):
            result = _record_hypothesis_tool(
                hypothesis="improve HRV",
                metric_key="hrv",
                user_id="unknown-user",
            )

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()


class TestGetOutcomesTool:
    """MCP tool: get_outcomes — lets Milo review past hypothesis results."""

    def test_registered_in_tool_registry(self):
        from mcp_server.tools import TOOL_REGISTRY, _get_outcomes_tool
        assert "get_outcomes" in TOOL_REGISTRY
        assert TOOL_REGISTRY["get_outcomes"] is _get_outcomes_tool

    def test_returns_outcomes(self, db_with_baseline):
        from unittest.mock import patch
        from engine.coaching.outcomes import record_hypothesis
        from mcp_server.tools import _get_outcomes_tool

        # Seed a hypothesis
        record_hypothesis(db_with_baseline, "andrew-001", "improve HRV", "hrv")

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_baseline), \
             patch("engine.gateway.db.init_db"):
            result = _get_outcomes_tool(user_id="andrew")

        assert result["count"] == 1
        assert result["outcomes"][0]["hypothesis"] == "improve HRV"
        assert result["outcomes"][0]["metric_key"] == "hrv"

    def test_empty_when_no_outcomes(self, db_with_andrew):
        from unittest.mock import patch
        from mcp_server.tools import _get_outcomes_tool

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_andrew), \
             patch("engine.gateway.db.init_db"):
            result = _get_outcomes_tool(user_id="andrew")

        assert result["count"] == 0
        assert result["outcomes"] == []

    def test_days_filter(self, db_with_baseline):
        from unittest.mock import patch
        from mcp_server.tools import _get_outcomes_tool

        # Insert an old hypothesis directly (60 days ago)
        old_date = (datetime.utcnow() - timedelta(days=60)).isoformat(timespec="seconds")
        db_with_baseline.execute(
            "INSERT INTO coaching_outcome (person_id, hypothesis, metric_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("andrew-001", "old hypothesis", "steps", old_date),
        )
        db_with_baseline.commit()

        with patch("mcp_server.tools._resolve_person_id", return_value="andrew-001"), \
             patch("engine.gateway.db.get_db", return_value=db_with_baseline), \
             patch("engine.gateway.db.init_db"):
            result = _get_outcomes_tool(user_id="andrew", days=30)

        assert result["count"] == 0  # 60-day-old outcome excluded by 30-day filter

    def test_requires_person(self):
        from unittest.mock import patch
        from mcp_server.tools import _get_outcomes_tool

        with patch("mcp_server.tools._resolve_person_id", return_value=None):
            result = _get_outcomes_tool(user_id="unknown-user")

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()
