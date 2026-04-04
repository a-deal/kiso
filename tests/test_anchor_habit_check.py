"""Tests for anchor habit awareness in scheduled messages.

The evening check-in should only ask about anchor habits when one exists.
Users without an anchor habit should get a prompt to pick one instead.
"""

import uuid
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from zoneinfo import ZoneInfo

from engine.gateway.scheduler import (
    get_anchor_habit,
)


_NOW = "2026-04-04T00:00:00Z"


def _insert_person(db, id, name, user_id, channel=None, target=None, tz="America/Los_Angeles"):
    db.execute(
        "INSERT INTO person (id, name, health_engine_user_id, channel, channel_target, timezone, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (id, name, user_id, channel, target, tz, _NOW, _NOW),
    )
    db.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    db_path = tmp_path / "data" / "kasane.db"
    from engine.gateway.db import init_db, get_db
    init_db(str(db_path))
    conn = get_db(str(db_path))
    return conn


@pytest.fixture
def db_with_mike(db):
    _insert_person(db, "mike-001", "Mike", "mike", "whatsapp", "+14155551234")
    return db


@pytest.fixture
def db_with_andrew(db):
    _insert_person(db, "andrew-001", "Andrew", "andrew", "whatsapp", "+14152009584")
    return db


class TestGetAnchorHabit:
    """get_anchor_habit returns the anchor habit title or None."""

    def test_no_focus_plan_returns_none(self, db_with_mike):
        result = get_anchor_habit(db_with_mike, "mike-001")
        assert result is None

    def test_focus_plan_with_anchor(self, db_with_andrew):
        now = datetime.utcnow().isoformat()
        db_with_andrew.execute(
            "INSERT INTO focus_plan (id, person_id, primary_anchor, primary_action, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp1", "andrew-001", "Wake by 6 AM", "Improve sleep consistency", now, now),
        )
        db_with_andrew.commit()

        result = get_anchor_habit(db_with_andrew, "andrew-001")
        assert result == "Wake by 6 AM"

    def test_deleted_focus_plan_returns_none(self, db_with_andrew):
        now = datetime.utcnow().isoformat()
        db_with_andrew.execute(
            "INSERT INTO focus_plan (id, person_id, primary_anchor, primary_action, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp1", "andrew-001", "Wake by 6 AM", "Improve sleep", now, now, now),
        )
        db_with_andrew.commit()

        result = get_anchor_habit(db_with_andrew, "andrew-001")
        assert result is None

    def test_focus_plan_without_anchor_returns_none(self, db_with_andrew):
        now = datetime.utcnow().isoformat()
        db_with_andrew.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("fp1", "andrew-001", "Improve sleep", now, now),
        )
        db_with_andrew.commit()

        result = get_anchor_habit(db_with_andrew, "andrew-001")
        assert result is None

    def test_returns_most_recent_anchor(self, db_with_andrew):
        now = datetime.utcnow().isoformat()
        db_with_andrew.execute(
            "INSERT INTO focus_plan (id, person_id, primary_anchor, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("fp1", "andrew-001", "Wake by 6 AM", "2026-03-01T00:00:00", now),
        )
        db_with_andrew.execute(
            "INSERT INTO focus_plan (id, person_id, primary_anchor, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("fp2", "andrew-001", "10K steps daily", "2026-04-01T00:00:00", now),
        )
        db_with_andrew.commit()

        result = get_anchor_habit(db_with_andrew, "andrew-001")
        assert result == "10K steps daily"
