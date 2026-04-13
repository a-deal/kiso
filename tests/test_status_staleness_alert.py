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

from mcp_server.tools import _get_status


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
