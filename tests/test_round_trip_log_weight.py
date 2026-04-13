"""Round-trip tripwire: _log_weight → build_briefing must surface the value.

This test exists because the dual-write era let writes land in CSV while
reads silently picked up SQLite (or vice versa), and Milo coached off
stale data for a week before anyone noticed. If this test fails, the
write path and the briefing read path are no longer talking to the same
store. Stop everything and diagnose before shipping.

Milestone 1 of the baseline consolidation sprint
(hub/plans/2026-04-12-baseline-consolidation.md).
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from engine.gateway.db import close_db, get_db, init_db


@pytest.fixture
def tmp_db_and_data(tmp_path):
    """Tmp SQLite db + tmp data_dir + a person row for user 'andrew'."""
    close_db()
    db_path = tmp_path / "kasane.db"

    data_dir = tmp_path / "data" / "users" / "andrew"
    data_dir.mkdir(parents=True)
    (data_dir / "config.yaml").write_text("profile:\n  age: 35\n  sex: M\n")

    with patch("engine.gateway.db._db_path", return_value=db_path):
        init_db(db_path)
        conn = get_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("andrew-deal-001", "Andrew", "andrew", now, now),
        )
        conn.commit()
        yield db_path, data_dir
    close_db()


def test_log_weight_round_trips_into_briefing(tmp_db_and_data):
    """The exact lbs value written by _log_weight must appear in briefing['weight']['current'].

    This is the canary that would have caught the 5+ lb drift incident on day one.
    """
    db_path, data_dir = tmp_db_and_data
    from datetime import timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    from mcp_server.tools import _log_weight
    from engine.coaching.briefing import build_briefing

    with patch("engine.gateway.db._db_path", return_value=db_path), \
         patch("mcp_server.tools._data_dir", return_value=data_dir):
        # Two entries: briefing builds the weight section only with >=2 rows.
        _log_weight(193.4, date=yesterday, user_id="andrew")
        result = _log_weight(192.7, date=today, user_id="andrew")
        assert result["logged"] is True

        config = {
            "data_dir": str(data_dir),
            "profile": {"age": 35, "sex": "M"},
        }
        briefing = build_briefing(config)

    assert "weight" in briefing, f"briefing has no 'weight' section: keys={list(briefing.keys())}"
    assert briefing["weight"].get("current") == pytest.approx(192.7), (
        f"Round-trip drift detected: wrote 192.7, briefing returned "
        f"{briefing['weight'].get('current')}. Write path and read path are not "
        f"reading the same store."
    )
