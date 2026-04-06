"""Tests for outbound message validation gate.

The gate checks every outbound Milo message for system internals leaking
into user-facing coaching messages. Three categories:

1. Machine output: JSON blobs, SQL, stack traces, log lines
2. Internal vocabulary: DB column names, API paths, Python identifiers
3. Structural anomalies: diagnostic dumps, abnormal length
"""

import pytest

from engine.gateway.outbound_gate import validate_outbound, ValidationResult


class TestMachineOutputDetection:
    """Messages containing raw machine output should be flagged."""

    def test_json_blob(self):
        msg = 'Here are your results: {"wearable_token": "abc", "status": "ok"}'
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_multiline_json(self):
        msg = "Check this out:\n{\n  \"user_id\": \"andrew\",\n  \"sleep_hrs\": 6.2\n}"
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_stack_trace(self):
        msg = "Something went wrong:\nTraceback (most recent call last):\n  File \"server.py\", line 42"
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_python_error(self):
        msg = "I ran into an issue: ModuleNotFoundError: No module named 'garth'"
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_sql_fragment(self):
        msg = "I checked SELECT COUNT(*) FROM wearable_daily WHERE user_id = 'andrew'"
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_log_line(self):
        msg = "2026-04-04 05:54:36 INFO health-engine.scheduler: sent morning brief for andrew"
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags

    def test_normal_message_not_flagged(self):
        msg = "Good morning! Your HRV is at 64ms, up from 58 last week. Sleep was 6.2 hours."
        result = validate_outbound(msg)
        assert result.ok


class TestInternalVocabularyLeak:
    """Messages referencing internal system names should be flagged."""

    def test_db_column_names(self):
        msg = "Your wearable_token is expired and person_id wasn't found in the database."
        result = validate_outbound(msg)
        assert not result.ok
        assert "internal_vocabulary" in result.flags

    def test_api_paths(self):
        msg = "I checked /health/deep and everything looks fine."
        result = validate_outbound(msg)
        assert not result.ok
        assert "internal_vocabulary" in result.flags

    def test_service_names(self):
        msg = "The gunicorn workers are running and openclaw is responding."
        result = validate_outbound(msg)
        assert not result.ok
        assert "internal_vocabulary" in result.flags

    def test_python_none_true_false(self):
        msg = "Your sleep data returned None and the token refresh returned True."
        result = validate_outbound(msg)
        assert not result.ok
        assert "internal_vocabulary" in result.flags

    def test_function_names(self):
        msg = "I called _get_daily_snapshot and sync_garmin_tokens for your account."
        result = validate_outbound(msg)
        assert not result.ok
        assert "internal_vocabulary" in result.flags

    def test_health_vocabulary_is_fine(self):
        """Words like 'sleep', 'garmin', 'heart rate' are user-facing."""
        msg = "Your Garmin shows heart rate at 48 bpm. Sleep was solid at 7.5 hours."
        result = validate_outbound(msg)
        assert result.ok

    def test_dashboard_url_is_fine(self):
        """Dashboard links are intentionally user-facing."""
        msg = "Check your dashboard: https://dashboard.mybaseline.health/dashboard/member.html"
        result = validate_outbound(msg)
        assert result.ok


class TestStructuralAnomalies:
    """Messages with structural red flags should be flagged."""

    def test_diagnostic_dump_keywords(self):
        msg = (
            "Analysis of Deep Health Check (2026-04-04)\n\n"
            "Problems Identified:\n"
            "1. user_data stale (>72h) — PROBLEM\n"
            "2. garmin_tokens: healthy\n"
            "Auto-remediation status: cron re-triggered."
        )
        result = validate_outbound(msg)
        assert not result.ok
        # Should flag both structural and internal vocabulary
        assert len(result.flags) >= 1

    def test_short_coaching_message_fine(self):
        msg = "Nice work on the 7.5 hours last night. HRV is recovering. Keep the same bedtime tonight."
        result = validate_outbound(msg)
        assert result.ok

    def test_http_status_codes(self):
        msg = "The API returned a 500 Internal Server Error when I tried to fetch your data."
        result = validate_outbound(msg)
        assert not result.ok
        assert "machine_output" in result.flags


class TestEdgeCases:
    """Ensure we don't over-flag normal coaching messages."""

    def test_metric_numbers_ok(self):
        msg = "RHR 48, HRV 64ms, sleep 6.2hrs, steps 9500. All trending well this week."
        result = validate_outbound(msg)
        assert result.ok

    def test_supplement_advice_ok(self):
        msg = "Take your magnesium glycinate 30 min before bed. Your sleep onset latency suggests it's helping."
        result = validate_outbound(msg)
        assert result.ok

    def test_training_advice_ok(self):
        msg = "Today's a training day (Saturday). 3 PM cortisol spike from the session should resolve by 9 PM. Bed by 10:30."
        result = validate_outbound(msg)
        assert result.ok

    def test_wearable_mention_ok(self):
        """Mentioning Garmin/Oura/Whoop by name is fine in coaching context."""
        msg = "Your Garmin synced this morning. Looks like you got 0.9 hours of deep sleep, which is low for you."
        result = validate_outbound(msg)
        assert result.ok

    def test_empty_message(self):
        result = validate_outbound("")
        assert result.ok

    def test_emoji_message(self):
        msg = "Great job today! 💪"
        result = validate_outbound(msg)
        assert result.ok

    def test_coaching_with_from_where_not_sql(self):
        """Weight/lab coaching that mentions 'from' and numbers shouldn't trigger SQL."""
        msg = (
            "Your triglycerides dropped from 120 to 70 mg/dL over that same period, "
            "hs-CRP went from 1.3 to 0.2, and ApoB came down 26 points."
        )
        result = validate_outbound(msg)
        assert result.ok

    def test_weekly_review_not_sql(self):
        """Weekly reviews with trends shouldn't trigger SQL."""
        msg = (
            "Weight is sitting at 189.9 lbs, down about 2.9 lbs over the past week "
            "and 4.7 lbs over 30 days. The 7-day rolling average sits at 190.0 where "
            "you're 1.9 lbs from your 188 target."
        )
        result = validate_outbound(msg)
        assert result.ok


class TestFalsePositiveFixes:
    """Fix false positives identified from 7-day production audit (April 4, 2026).

    Three categories causing 32/51 false flags (63%):
    1. Python literals (True/False/None) in natural language
    2. user_id appearing in auth URLs (query params)
    3. openclaw in troubleshooting context
    """

    # --- Python literals in natural language ---

    def test_natural_true_not_flagged(self):
        msg = "That's true. Higher carb days literally pump more water into muscle tissue."
        result = validate_outbound(msg)
        assert result.ok, f"False positive: natural 'true' flagged: {result.details}"

    def test_natural_false_not_flagged(self):
        msg = "That's false. You don't need to fast before a lipid panel anymore."
        result = validate_outbound(msg)
        assert result.ok, f"False positive: natural 'false' flagged: {result.details}"

    def test_natural_none_not_flagged(self):
        msg = "None of your metrics are in the red zone this week."
        result = validate_outbound(msg)
        assert result.ok, f"False positive: natural 'none' flagged: {result.details}"

    def test_python_none_in_code_context_still_flagged(self):
        """'returned None' or 'is None' is still a code leak."""
        msg = "Your sleep data returned None and the refresh failed."
        result = validate_outbound(msg)
        assert not result.ok

    def test_python_true_in_code_context_still_flagged(self):
        """'returned True' or '= True' is still a code leak."""
        msg = "The token refresh returned True but the data is empty."
        result = validate_outbound(msg)
        assert not result.ok

    # --- user_id in auth URLs ---

    def test_auth_url_with_user_id_not_flagged(self):
        msg = (
            "Tap this link to connect your Garmin:\n"
            "https://auth.mybaseline.health/auth/garmin?user=paul&state=paul:garmin:abc123"
        )
        result = validate_outbound(msg)
        assert result.ok, f"False positive: user_id in auth URL flagged: {result.details}"

    def test_user_id_outside_url_still_flagged(self):
        """Bare user_id references are still system internals."""
        msg = "I need to add user_id to the calendar call. Let me retry."
        result = validate_outbound(msg)
        assert not result.ok

    # --- openclaw in troubleshooting context ---

    def test_whatsapp_down_openclaw_mention_not_flagged(self):
        msg = (
            "WhatsApp is down. Your morning brief couldn't be delivered. "
            "The OpenClaw WhatsApp listener needs to be reconnected."
        )
        result = validate_outbound(msg)
        assert result.ok, f"False positive: openclaw in user troubleshooting: {result.details}"

    def test_openclaw_in_system_diagnostic_still_flagged(self):
        """OpenClaw in a diagnostic dump context is still a leak."""
        msg = "The openclaw gateway status shows 3 sessions. Cron re-triggered auto-remediation."
        result = validate_outbound(msg)
        assert not result.ok


class TestIngestIntegration:
    """Verify the gate is wired into _ingest_message."""

    @pytest.fixture
    def test_db(self, tmp_path, monkeypatch):
        from engine.gateway.db import init_db, close_db, get_db
        close_db()
        db_path = tmp_path / "test.db"
        init_db(db_path)
        monkeypatch.setattr(
            "engine.gateway.db._db_path", lambda: db_path,
        )
        # Add a known user so _ingest_message can resolve user_id
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        db = get_db(db_path)
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, phone, channel, channel_target, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("mike-001", "Mike", "mike", "+17033625977", "whatsapp", "+17033625977", now, now),
        )
        db.commit()
        yield db_path
        close_db()

    def test_clean_message_no_flags(self, test_db):
        from mcp_server.tools import _ingest_message
        result = _ingest_message(
            role="assistant",
            content="Your HRV is at 64ms. Sleep was 6.2 hours. Keep it up.",
            sender_name="Milo",
            session_key="agent:main:whatsapp:direct:+17033625977",
        )
        assert result["status"] == "ok"
        assert "gate_flags" not in result

    def test_flagged_message_has_gate_data(self, test_db):
        from mcp_server.tools import _ingest_message
        result = _ingest_message(
            role="assistant",
            content='Here is the data: {"wearable_token": "abc", "person_id": "p1"}',
            sender_name="Milo",
            session_key="agent:main:whatsapp:direct:+17033625977",
        )
        assert result["status"] == "ok"  # Still ingested (async audit, not blocking)
        assert "gate_flags" in result
        assert "machine_output" in result["gate_flags"]

    def test_user_messages_not_gated(self, test_db):
        from mcp_server.tools import _ingest_message
        result = _ingest_message(
            role="user",
            content='{"wearable_token": "abc"}',  # User can say whatever they want
            sender_id="+17033625977",
            sender_name="Mike",
        )
        assert result["status"] == "ok"
        assert "gate_flags" not in result
