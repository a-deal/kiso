"""Tests for /health/deep endpoint coverage.

Verifies health/deep checks all critical subsystems:
database, user data, garmin tokens, apple health, audit log, disk,
scheduler, and briefing freshness.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from engine.gateway.config import GatewayConfig
from engine.gateway.db import init_db, get_db, close_db
from engine.gateway.server import create_app

TOKEN = "test-token-health"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "kasane.db"
    init_db(path)
    return path


@pytest.fixture
def health_client(db_path, tmp_path, monkeypatch):
    """TestClient with a temp database and data directory for health/deep."""
    monkeypatch.setattr("engine.gateway.db._db_path", lambda: db_path)

    import engine.gateway.v1_api as v1_mod
    monkeypatch.setattr(v1_mod, "get_db", lambda p=None: get_db(db_path))

    config = GatewayConfig(port=18899, api_token=TOKEN)
    app = create_app(config)
    return TestClient(app)


class TestHealthDeepCovers:
    """health/deep must include checks for all critical subsystems."""

    def test_has_scheduler_check(self, health_client):
        """health/deep should report scheduler status."""
        resp = health_client.get("/health/deep")
        assert resp.status_code == 200
        data = resp.json()
        assert "scheduler" in data["checks"], \
            f"health/deep missing 'scheduler' check. Keys: {list(data['checks'].keys())}"

    def test_has_briefing_freshness_check(self, health_client):
        """health/deep should report briefing freshness per active user."""
        resp = health_client.get("/health/deep")
        assert resp.status_code == 200
        data = resp.json()
        assert "briefing_freshness" in data["checks"], \
            f"health/deep missing 'briefing_freshness' check. Keys: {list(data['checks'].keys())}"

    def test_scheduler_reports_last_send(self, db_path, health_client):
        """Scheduler check should show last successful send time."""
        db = get_db(db_path)
        now = datetime.now(timezone.utc)
        db.execute(
            "INSERT INTO scheduled_send (person_id, schedule_type, sent_date, sent_at, status, message_preview) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("p1", "morning_brief", now.strftime("%Y-%m-%d"), now.isoformat(), "sent", "test"),
        )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        sched = data["checks"]["scheduler"]
        assert sched["status"] in ("ok", "stale")
        assert "last_send_hours_ago" in sched

    def test_scheduler_stale_when_no_sends(self, health_client):
        """Scheduler with no sends should report stale."""
        resp = health_client.get("/health/deep")
        data = resp.json()
        sched = data["checks"]["scheduler"]
        assert sched["status"] == "no_sends"


class TestHealthDeepSourceChanges:
    """health/deep should detect wearable source changes per user."""

    def test_reports_source_change(self, db_path, health_client):
        """When a user's wearable source changed recently, health/deep should flag it."""
        import uuid
        db = get_db(db_path)
        now_str = datetime.now(timezone.utc).isoformat()

        # Insert a person
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p1", "Andrew", "andrew", now_str, now_str),
        )
        # Insert wearable data with source change (relative dates so test doesn't go stale)
        from datetime import timedelta
        today = datetime.now(timezone.utc).date()
        for i, (date, source, vo2) in enumerate([
            (str(today - timedelta(days=4)), "garmin", 47.0),
            (str(today - timedelta(days=3)), "garmin", 47.0),
            (str(today - timedelta(days=1)), "apple_health", 32.3),
        ]):
            rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"p1:wearable_daily:{date}:{source}"))
            db.execute(
                "INSERT INTO wearable_daily (id, person_id, date, source, vo2_max, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, "p1", date, source, vo2, now_str, now_str),
            )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        assert "wearable_source_changes" in data["checks"], \
            f"Missing wearable_source_changes check. Keys: {list(data['checks'].keys())}"
        sc = data["checks"]["wearable_source_changes"]
        assert "andrew" in sc
        assert sc["andrew"]["status"] == "changed"
        assert "vo2_max" in sc["andrew"]["changes"]

    def test_no_source_change_reports_ok(self, db_path, health_client):
        """When all data comes from one source, status should be ok."""
        import uuid
        db = get_db(db_path)
        now_str = datetime.now(timezone.utc).isoformat()

        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p1", "Andrew", "andrew", now_str, now_str),
        )
        for date in ["2026-04-01", "2026-04-02", "2026-04-03"]:
            rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"p1:wearable_daily:{date}:garmin"))
            db.execute(
                "INSERT INTO wearable_daily (id, person_id, date, source, vo2_max, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, "p1", date, "garmin", 47.0, now_str, now_str),
            )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        sc = data["checks"].get("wearable_source_changes", {})
        # Either not present (no changes) or ok
        if "andrew" in sc:
            assert sc["andrew"]["status"] == "ok"

    def test_no_users_no_crash(self, health_client):
        """health/deep should not crash when there are no users with wearable data."""
        resp = health_client.get("/health/deep")
        assert resp.status_code == 200


# TestStaleCriticalFileMaskingBug removed 2026-04-13: the three tests in
# this class (from commit 772d64c) asserted that stale apple_health_latest.json
# / garmin_latest.json are flagged via the per-file tripwire. Those files
# were retired on Apr 3 2026 in commit 893f215 ("Remove JSON writes from
# wearable integrations"). With _FRESHNESS_TRACKED now empty, the tripwire
# has no files to fire on, and the regression the tests were guarding is no
# longer reachable by construction. When Milestone 6 repoints freshness at
# a SQLite-era surface, a new regression test should be written against
# THAT surface, not this one.


class TestStuckUserDetection:
    """health/deep should flag users who have been in the system >48h with no data."""

    def test_stuck_user_flagged(self, db_path, health_client):
        """User created 3 days ago with zero wearable data should be flagged as stuck."""
        db = get_db(db_path)
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, channel, channel_target, timezone, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-stuck", "StuckUser", "stuck", "whatsapp", "+15551234567", "America/Los_Angeles", three_days_ago, three_days_ago),
        )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        assert "stuck_users" in data["checks"], \
            f"Missing stuck_users check. Keys: {list(data['checks'].keys())}"
        assert "stuck" in data["checks"]["stuck_users"]
        assert data["checks"]["stuck_users"]["stuck"]["status"] == "stuck"
        assert data["checks"]["stuck_users"]["stuck"]["days"] >= 2

    def test_new_user_not_flagged(self, db_path, health_client):
        """User created 1 hour ago should not be flagged."""
        db = get_db(db_path)
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, channel, channel_target, timezone, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-new", "NewUser", "newbie", "whatsapp", "+15559876543", "America/Los_Angeles", one_hour_ago, one_hour_ago),
        )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        stuck = data["checks"].get("stuck_users", {})
        assert "newbie" not in stuck

    def test_active_user_not_flagged(self, db_path, health_client):
        """User created 5 days ago WITH wearable data should not be flagged."""
        import uuid
        db = get_db(db_path)
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        db.execute(
            "INSERT INTO person (id, name, health_engine_user_id, channel, channel_target, timezone, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p-active", "ActiveUser", "active", "whatsapp", "+15550001111", "America/Los_Angeles", five_days_ago, five_days_ago),
        )
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        rid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO wearable_daily (id, person_id, date, source, rhr, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, "p-active", yesterday, "garmin", 62, five_days_ago, five_days_ago),
        )
        db.commit()

        resp = health_client.get("/health/deep")
        data = resp.json()
        stuck = data["checks"].get("stuck_users", {})
        assert "active" not in stuck
