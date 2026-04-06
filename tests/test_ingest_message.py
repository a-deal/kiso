"""Tests for _ingest_message write-side validation.

Verifies that messages with unresolvable user_id are logged and rejected,
not silently written with NULL user_id. The read-side filter (null_filter
in _get_conversations) is a safety net, not the primary defense.
"""

from datetime import datetime, timezone

import pytest

from engine.gateway.db import init_db, get_db, close_db
from mcp_server.tools import _ingest_message


@pytest.fixture
def msg_db(tmp_path, monkeypatch):
    """Fresh DB with person + phone map for message ingestion."""
    close_db()
    monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)

    db_path = tmp_path / "data" / "kasane.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(str(db_path))
    db = get_db(str(db_path))

    now = datetime.now(timezone.utc).isoformat()

    # Create a known user so we can test both resolved and unresolved paths
    db.execute(
        "INSERT INTO person (id, name, health_engine_user_id, phone, channel, channel_target, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("andrew-001", "Andrew", "andrew", "+14152009584", "whatsapp", "+14152009584", now, now),
    )
    db.commit()

    yield db
    close_db()


class TestUnresolvedUserRejection:
    """Messages with unresolvable sender must not be written with NULL user_id."""

    def test_unknown_sender_returns_unresolved(self, msg_db):
        """An unknown phone number should be flagged, not silently stored."""
        result = _ingest_message(
            role="user",
            content="hello from unknown",
            sender_id="+19995551234",
            sender_name="Unknown Person",
            channel="whatsapp",
        )
        assert result["status"] == "unresolved_user"
        assert result["user_id"] is None

    def test_unknown_sender_not_in_db(self, msg_db):
        """Unresolved messages should not be inserted into conversation_message."""
        _ingest_message(
            role="user",
            content="ghost message",
            sender_id="+19995551234",
            sender_name="Ghost",
            channel="whatsapp",
        )
        count = msg_db.execute(
            "SELECT COUNT(*) as c FROM conversation_message WHERE content = 'ghost message'"
        ).fetchone()["c"]
        assert count == 0

    def test_assistant_no_session_key_returns_unresolved(self, msg_db):
        """Assistant messages without a session_key can't resolve a user."""
        result = _ingest_message(
            role="assistant",
            content="cron output with no session",
            sender_name="Milo",
        )
        assert result["status"] == "unresolved_user"

    def test_assistant_cron_session_key_returns_unresolved(self, msg_db):
        """Cron session keys don't contain a phone number to resolve."""
        result = _ingest_message(
            role="assistant",
            content="K journal synthesis",
            sender_name="k",
            session_key="agent:k:cron:some-uuid",
        )
        assert result["status"] == "unresolved_user"


class TestResolvedUserAccepted:
    """Messages with resolvable sender should be written normally."""

    def test_known_sender_stored(self, msg_db):
        """A known phone number resolves and the message is stored."""
        result = _ingest_message(
            role="user",
            content="how am I doing?",
            sender_id="+14152009584",
            sender_name="Andrew",
            channel="whatsapp",
        )
        assert result["status"] == "ok"
        assert result["user_id"] == "andrew"

        row = msg_db.execute(
            "SELECT user_id, content FROM conversation_message WHERE content = 'how am I doing?'"
        ).fetchone()
        assert row is not None
        assert row["user_id"] == "andrew"

    def test_assistant_with_valid_session_key_stored(self, msg_db):
        """Assistant messages with a phone in session_key resolve correctly."""
        result = _ingest_message(
            role="assistant",
            content="Sleep was 7.2 hours",
            sender_name="Milo",
            session_key="agent:main:whatsapp:direct:+14152009584",
        )
        assert result["status"] == "ok"
        assert result["user_id"] == "andrew"
