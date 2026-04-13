"""Timestamp-grounding tripwire for _get_status.

Background: during the Saturday debugging session Milo "narrated" a file
timestamp ("modified at 3:22 PM today") that did not match os.stat
(real mtime: Mar 25, 18 days earlier). System-diagnosis surfaces must
return raw mtimes, not computed/rounded strings, so callers can verify
against ground truth.

This test pins the contract: _get_status returns mtime_unix that equals
os.stat(file).st_mtime exactly. Milestone 1 of the baseline consolidation
sprint (hub/plans/2026-04-12-baseline-consolidation.md).
"""

import os
from unittest.mock import patch

import pytest

from mcp_server.tools import _get_status


@pytest.fixture
def fake_data_dir(tmp_path):
    """A data dir with a stale apple_health_latest.json at a known mtime."""
    d = tmp_path / "data" / "users" / "tg_user"
    d.mkdir(parents=True)
    f = d / "apple_health_latest.json"
    f.write_text('{"weight": 192.7}')
    # Pin mtime to a known value 200 hours ago.
    fake_mtime = 1_700_000_000.0  # Tue Nov 14 14:13:20 2023 UTC, deterministic
    os.utime(f, (fake_mtime, fake_mtime))
    return d, f, fake_mtime


def test_get_status_returns_raw_mtime_unix(fake_data_dir):
    data_dir, f, fake_mtime = fake_data_dir

    with patch("mcp_server.tools._data_dir", return_value=data_dir):
        # user_id=None to avoid _user_dir mkdir leaking into real data/users.
        status = _get_status(user_id=None)

    files = status["files"]
    entry = files["apple_health_latest.json"]
    assert entry["exists"] is True
    assert "mtime_unix" in entry, (
        "_get_status must expose raw mtime_unix from os.stat. Without this, "
        "downstream consumers reason about freshness from a rounded human "
        "string and Milo can hallucinate a timestamp that doesn't match disk."
    )
    # Must match os.stat exactly to the second (it's the same float).
    assert entry["mtime_unix"] == fake_mtime
    assert entry["mtime_unix"] == os.stat(f).st_mtime
