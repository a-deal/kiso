"""Tests for user-stated goals: extraction, storage, and scheduler integration."""

import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

from engine.gateway.scheduler import get_anchor_habit


_NOW = "2026-04-07T00:00:00Z"


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
def db_with_paul(db):
    _insert_person(db, "paul-001", "Paul", "paul", "whatsapp", "+17038878948")
    return db


@pytest.fixture
def db_with_andrew(db):
    _insert_person(db, "andrew-001", "Andrew", "andrew", "whatsapp", "+14152009584")
    return db


# --- set_user_goals tests ---


class TestSetUserGoals:

    def test_registered_in_tool_registry(self):
        from mcp_server.tools import TOOL_REGISTRY, _set_user_goals
        assert "set_user_goals" in TOOL_REGISTRY
        assert TOOL_REGISTRY["set_user_goals"] is _set_user_goals

    def test_writes_focus_plan_with_user_stated_origin(self, db_with_paul, monkeypatch):
        from mcp_server.tools import _set_user_goals
        monkeypatch.setattr("mcp_server.tools._resolve_person_id", lambda uid: "paul-001")
        result = _set_user_goals(
            goals="3x strength training/week, daily fish oil",
            user_id="paul",
        )
        assert result["saved"] is True
        assert result["origin"] == "user_stated"

        row = db_with_paul.execute(
            "SELECT primary_action, origin, exclusions FROM focus_plan WHERE person_id = 'paul-001' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row["primary_action"] == "3x strength training/week, daily fish oil"
        assert row["origin"] == "user_stated"

    def test_writes_exclusions(self, db_with_paul, monkeypatch):
        from mcp_server.tools import _set_user_goals
        monkeypatch.setattr("mcp_server.tools._resolve_person_id", lambda uid: "paul-001")
        result = _set_user_goals(
            goals="3x strength training/week",
            exclusions="weight tracking",
            user_id="paul",
        )
        assert result["saved"] is True

        row = db_with_paul.execute(
            "SELECT exclusions FROM focus_plan WHERE person_id = 'paul-001' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row["exclusions"] == "weight tracking"

    def test_coexists_with_ios_generated(self, db_with_paul, monkeypatch):
        """User-stated goals don't delete iOS-generated plans."""
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, primary_anchor, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp-ios", "paul-001", "Walk 10K steps daily", "After breakfast", "ios_generated", "2026-04-01T00:00:00Z", now),
        )
        db_with_paul.commit()

        from mcp_server.tools import _set_user_goals
        monkeypatch.setattr("mcp_server.tools._resolve_person_id", lambda uid: "paul-001")
        _set_user_goals(goals="3x strength training/week", user_id="paul")

        # Both should exist
        rows = db_with_paul.execute(
            "SELECT origin FROM focus_plan WHERE person_id = 'paul-001' AND deleted_at IS NULL ORDER BY created_at ASC"
        ).fetchall()
        origins = [r["origin"] for r in rows]
        assert "ios_generated" in origins
        assert "user_stated" in origins

    def test_recency_wins(self, db_with_paul, monkeypatch):
        """Most recent focus_plan is what get_user_goals returns."""
        from engine.gateway.scheduler import get_user_goals
        now = datetime.utcnow().isoformat()
        # Old iOS plan with an anchor
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, primary_anchor, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp-ios", "paul-001", "Walk daily", "After breakfast", "ios_generated", "2026-04-01T00:00:00Z", now),
        )
        db_with_paul.commit()

        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] == "Walk daily"
        assert result["origin"] == "ios_generated"

        # Newer user-stated plan
        from mcp_server.tools import _set_user_goals
        monkeypatch.setattr("mcp_server.tools._resolve_person_id", lambda uid: "paul-001")
        _set_user_goals(goals="3x strength training/week", user_id="paul")

        # get_user_goals should return the newer user-stated plan
        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] == "3x strength training/week"
        assert result["origin"] == "user_stated"

        # get_anchor_habit still returns the iOS anchor (it filters for non-null anchors)
        assert get_anchor_habit(db_with_paul, "paul-001") == "After breakfast"

    def test_unknown_user_returns_error(self, db_with_paul, monkeypatch):
        from mcp_server.tools import _set_user_goals
        monkeypatch.setattr("mcp_server.tools._resolve_person_id", lambda uid: None)
        result = _set_user_goals(goals="some goals", user_id="nobody")
        assert "error" in result


# --- get_user_goals tests ---


class TestGetUserGoals:

    def test_no_focus_plan_returns_empty(self, db_with_paul):
        from engine.gateway.scheduler import get_user_goals
        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] is None
        assert result["exclusions"] is None
        assert result["anchor"] is None

    def test_returns_latest_plan_goals(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, primary_anchor, exclusions, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "3x strength/week, fish oil", None, "weight tracking", "user_stated", now, now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_user_goals
        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] == "3x strength/week, fish oil"
        assert result["exclusions"] == "weight tracking"
        assert result["origin"] == "user_stated"

    def test_recency_across_origins(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        # Older iOS plan
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, primary_anchor, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "Walk daily", "After breakfast", "ios_generated", "2026-04-01T00:00:00Z", now),
        )
        # Newer user-stated plan
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, exclusions, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp2", "paul-001", "3x strength/week", "weight", "user_stated", "2026-04-07T00:00:00Z", now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_user_goals
        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] == "3x strength/week"
        assert result["exclusions"] == "weight"
        assert result["origin"] == "user_stated"

    def test_deleted_plan_skipped(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "deleted goals", "user_stated", now, now, now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_user_goals
        result = get_user_goals(db_with_paul, "paul-001")
        assert result["goals"] is None


# --- Composition context tests ---


class TestCompositionIncludesUserContext:
    """Verify that _compose_message receives user replies and exclusions."""

    def test_exclusions_in_prompt(self):
        """When exclusions are provided, they appear in the Sonnet prompt."""
        from engine.gateway.scheduler import _compose_message
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Great session today.")]
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            _compose_message(
                "evening_checkin", "Paul", {"checkin": {}},
                exclusions="weight tracking",
            )

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "weight tracking" in user_content
        assert "Do NOT" in user_content or "NEVER" in user_content

    def test_user_replies_in_prompt(self):
        """When recent user replies are provided, they appear in context."""
        from engine.gateway.scheduler import _compose_message
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Focus on strength today.")]
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            _compose_message(
                "morning_brief", "Paul", {"checkin": {}},
                recent_user_replies=["no need to step on weight, not my program", "focused on 3x strength"],
            )

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "not my program" in user_content
        assert "3x strength" in user_content


# --- Reconciliation diff tests ---


class TestGetUnreconciledGoalsTool:

    def test_registered_in_tool_registry(self):
        from mcp_server.tools import TOOL_REGISTRY, _get_unreconciled_goals
        assert "get_unreconciled_goals" in TOOL_REGISTRY
        assert TOOL_REGISTRY["get_unreconciled_goals"] is _get_unreconciled_goals

    def test_returns_count_and_list(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "3x strength/week", "user_stated", now, now),
        )
        db_with_paul.commit()

        from mcp_server.tools import _get_unreconciled_goals
        result = _get_unreconciled_goals()
        assert result["count"] >= 1
        assert any(g["person_id"] == "paul-001" for g in result["unreconciled"])


class TestReconciliationDiff:

    def test_no_plans_returns_empty(self, db_with_paul):
        from engine.gateway.scheduler import get_unreconciled_goals
        result = get_unreconciled_goals(db_with_paul)
        assert result == []

    def test_user_stated_without_ios_flagged(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "3x strength/week", "user_stated", now, now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_unreconciled_goals
        result = get_unreconciled_goals(db_with_paul)
        assert len(result) == 1
        assert result[0]["person_id"] == "paul-001"
        assert result[0]["user_stated_goals"] == "3x strength/week"

    def test_reconciled_not_flagged(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        # User stated, then reconciled
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "3x strength/week", "user_stated", "2026-04-06T00:00:00Z", now),
        )
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp2", "paul-001", "3x strength/week, fish oil", "reconciled", "2026-04-07T00:00:00Z", now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_unreconciled_goals
        result = get_unreconciled_goals(db_with_paul)
        assert result == []

    def test_ios_generated_does_not_resolve_user_stated(self, db_with_paul):
        """An iOS-generated plan shouldn't auto-resolve unreconciled user-stated goals."""
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, exclusions, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "3x strength/week", "weight", "user_stated", "2026-04-06T00:00:00Z", now),
        )
        # Newer iOS plan that doesn't know about exclusions
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp2", "paul-001", "Walk 10K steps", "ios_generated", "2026-04-08T00:00:00Z", now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_unreconciled_goals
        result = get_unreconciled_goals(db_with_paul)
        assert len(result) == 1, "iOS plan should NOT resolve user-stated goals"
        assert result[0]["exclusions"] == "weight"

    def test_user_stated_newer_than_reconciled_flagged(self, db_with_paul):
        now = datetime.utcnow().isoformat()
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp1", "paul-001", "Walk daily", "reconciled", "2026-04-01T00:00:00Z", now),
        )
        db_with_paul.execute(
            "INSERT INTO focus_plan (id, person_id, primary_action, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fp2", "paul-001", "3x strength/week", "user_stated", "2026-04-07T00:00:00Z", now),
        )
        db_with_paul.commit()

        from engine.gateway.scheduler import get_unreconciled_goals
        result = get_unreconciled_goals(db_with_paul)
        assert len(result) == 1
