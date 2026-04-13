"""Staleness tripwire for _get_status.

Background: apple_health_latest.json was 18 days dead and Milo coached
off the dead surface for at least a week. The system-health bash check
fires alerts on user-dir freshness, but there was no Python-side
tripwire and the alerts that did fire were off by an order of magnitude.

This test pins the contract: when a freshness-tracked file is older
than 72h, _get_status returns a stale_warnings entry naming the file
and an age_hours computed from raw mtime. Milestone 1 of the baseline
consolidation sprint (hub/plans/2026-04-12-baseline-consolidation.md).
"""

import os
import time
from unittest.mock import patch

import pytest

from mcp_server.tools import _FRESHNESS_TRACKED, _get_status


@pytest.fixture
def stale_apple_health_dir(tmp_path):
    d = tmp_path / "data" / "users" / "stale_user"
    d.mkdir(parents=True)
    f = d / "apple_health_latest.json"
    f.write_text('{"weight": 192.7}')
    # Pin mtime 200 hours ago.
    fake_mtime = time.time() - (200 * 3600)
    os.utime(f, (fake_mtime, fake_mtime))
    return d


def test_get_status_fires_stale_warning_for_dead_apple_health(stale_apple_health_dir):
    with patch("mcp_server.tools._data_dir", return_value=stale_apple_health_dir):
        # user_id=None to avoid _load_config -> _user_dir mkdir side effect
        # in the real data/users tree.
        status = _get_status(user_id=None)

    warnings = status["stale_warnings"]
    apple = [w for w in warnings if w["file"] == "apple_health_latest.json"]
    assert len(apple) == 1, f"expected one stale warning for apple_health_latest.json, got {warnings}"
    w = apple[0]
    assert w["age_hours"] is not None
    assert 199 < w["age_hours"] < 201, f"age_hours should be ~200, got {w['age_hours']}"
    assert w["threshold_hours"] == 72
    assert "stale" in w["message"].lower() or "old" in w["message"].lower()


def test_get_status_no_warning_for_fresh_apple_health(tmp_path):
    d = tmp_path / "data" / "users" / "fresh_user"
    d.mkdir(parents=True)
    (d / "apple_health_latest.json").write_text('{"weight": 192.7}')

    with patch("mcp_server.tools._data_dir", return_value=d):
        status = _get_status(user_id=None)

    apple_warnings = [w for w in status["stale_warnings"] if w["file"] == "apple_health_latest.json"]
    assert apple_warnings == [], f"fresh file should not warn, got {apple_warnings}"


def test_freshness_tracked_membership_is_trimmed():
    """Pin the trimmed freshness set.

    garmin_daily.json, briefing.json, and lab_results.json were dropped:
    - garmin_daily.json is a derived rollup (garmin_latest.json is the
      real sync surface); double-alerting on both is noise.
    - briefing.json is regenerated on demand, not a sync target.
    - lab_results.json is event-driven, not a daily sync.

    Milestone 3 will move these behind a MAX(created_at) SQLite query and
    Milestone 6 unifies with system-health-check.sh. Until then, keep the
    tripwire narrow to the two surfaces that actually failed silently.
    """
    assert _FRESHNESS_TRACKED == {
        "apple_health_latest.json",
        "garmin_latest.json",
    }


def test_get_status_does_not_warn_for_stale_garmin_daily(tmp_path):
    """garmin_daily.json is no longer freshness-tracked."""
    d = tmp_path / "data" / "users" / "stale_user"
    d.mkdir(parents=True)
    f = d / "garmin_daily.json"
    f.write_text('{"steps": 4200}')
    fake_mtime = time.time() - (200 * 3600)
    os.utime(f, (fake_mtime, fake_mtime))

    with patch("mcp_server.tools._data_dir", return_value=d):
        status = _get_status(user_id=None)

    warnings = [w for w in status["stale_warnings"] if w["file"] == "garmin_daily.json"]
    assert warnings == [], f"garmin_daily.json should not be tracked, got {warnings}"


def test_get_status_does_not_warn_for_stale_briefing(tmp_path):
    """briefing.json is no longer freshness-tracked."""
    d = tmp_path / "data" / "users" / "stale_user"
    d.mkdir(parents=True)
    f = d / "briefing.json"
    f.write_text('{"summary": "stub"}')
    fake_mtime = time.time() - (200 * 3600)
    os.utime(f, (fake_mtime, fake_mtime))

    with patch("mcp_server.tools._data_dir", return_value=d):
        status = _get_status(user_id=None)

    warnings = [w for w in status["stale_warnings"] if w["file"] == "briefing.json"]
    assert warnings == [], f"briefing.json should not be tracked, got {warnings}"
