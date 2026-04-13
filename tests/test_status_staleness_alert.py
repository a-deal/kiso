"""Staleness tripwire for _get_status — post-JSON-retirement contract.

History: the original version of this file (Milestone 1 of the
baseline-consolidation sprint) pinned stale_warnings behavior for
apple_health_latest.json and garmin_latest.json. Those JSON sidecars were
retired on Apr 3 2026 in commit 893f215 ("Remove JSON writes from wearable
integrations"). On Apr 13 2026 the stale warnings fired as false positives
on ghost files, and _FRESHNESS_TRACKED was emptied in response. This file
now pins the post-retirement contract: no files are freshness-tracked, and
adding one back requires a deliberate change to _FRESHNESS_TRACKED.

Milestone 6 of the baseline-consolidation plan will re-establish freshness
tracking against SQLite (MAX(created_at) queries against wearable_daily /
apple_health tables). When that lands, replace these tests with new ones
pinning the SQLite-era contract.
"""

import os
import time
from unittest.mock import patch

import pytest

from mcp_server.tools import _FRESHNESS_TRACKED, _get_status


def test_freshness_tracked_is_empty_after_json_retirement():
    """Pin the empty set so re-adding a tracked file is a deliberate change.

    See commit 893f215 (Apr 3 2026) for the JSON retirement and the Apr 13
    retro for why the previously-tracked files became false positives.
    """
    assert _FRESHNESS_TRACKED == set()


def test_get_status_does_not_warn_for_stale_garmin_daily(tmp_path):
    """garmin_daily.json is not freshness-tracked (derived rollup)."""
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
    """briefing.json is not freshness-tracked (regenerated on demand)."""
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


def test_get_status_does_not_warn_for_stale_apple_health_json(tmp_path):
    """Retired: apple_health_latest.json is no longer freshness-tracked.

    Commit 893f215 removed the JSON write path. A stale file on disk is a
    ghost from pre-retirement and must not produce an alert.
    """
    d = tmp_path / "data" / "users" / "stale_user"
    d.mkdir(parents=True)
    f = d / "apple_health_latest.json"
    f.write_text('{"weight": 192.7}')
    fake_mtime = time.time() - (200 * 3600)
    os.utime(f, (fake_mtime, fake_mtime))

    with patch("mcp_server.tools._data_dir", return_value=d):
        status = _get_status(user_id=None)

    warnings = [w for w in status["stale_warnings"] if w["file"] == "apple_health_latest.json"]
    assert warnings == [], f"retired file should not be tracked, got {warnings}"


def test_get_status_does_not_warn_for_stale_garmin_latest_json(tmp_path):
    """Retired: garmin_latest.json is no longer freshness-tracked.

    Commit 893f215 removed the JSON write path. Mirror of the apple_health
    test above.
    """
    d = tmp_path / "data" / "users" / "stale_user"
    d.mkdir(parents=True)
    f = d / "garmin_latest.json"
    f.write_text('{"resting_hr": 55}')
    fake_mtime = time.time() - (200 * 3600)
    os.utime(f, (fake_mtime, fake_mtime))

    with patch("mcp_server.tools._data_dir", return_value=d):
        status = _get_status(user_id=None)

    warnings = [w for w in status["stale_warnings"] if w["file"] == "garmin_latest.json"]
    assert warnings == [], f"retired file should not be tracked, got {warnings}"
