"""Tests for delete_weight tool."""

from unittest.mock import patch
from pathlib import Path

import pytest

from mcp_server.tools import _log_weight, _delete_weight


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data" / "users" / "test_user"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def patched(data_dir):
    with patch("mcp_server.tools._data_dir", return_value=data_dir):
        yield data_dir


def test_delete_existing_weight(patched):
    """Delete a weight entry that exists."""
    _log_weight(185.0, date="2026-04-01", user_id="test_user")
    result = _delete_weight(date="2026-04-01", user_id="test_user")
    assert result["deleted"] is True

    # Re-log and verify only one entry remains
    _log_weight(155.0, date="2026-04-02", user_id="test_user")
    from engine.utils.csv_io import read_csv
    rows = read_csv(patched / "weight_log.csv")
    dates = [r["date"] for r in rows]
    assert "2026-04-01" not in dates
    assert "2026-04-02" in dates


def test_delete_nonexistent_weight(patched):
    """Delete a date that has no entry."""
    result = _delete_weight(date="2026-01-01", user_id="test_user")
    assert result["deleted"] is False


def test_delete_requires_date(patched):
    """Date is required."""
    result = _delete_weight(date=None, user_id="test_user")
    assert "error" in result


def test_delete_preserves_other_entries(patched):
    """Deleting one date shouldn't affect others."""
    _log_weight(160.0, date="2026-03-01", user_id="test_user")
    _log_weight(185.0, date="2026-04-01", user_id="test_user")
    _log_weight(155.0, date="2026-04-02", user_id="test_user")

    _delete_weight(date="2026-04-01", user_id="test_user")

    from engine.utils.csv_io import read_csv
    rows = read_csv(patched / "weight_log.csv")
    assert len(rows) == 2
    dates = [r["date"] for r in rows]
    assert "2026-03-01" in dates
    assert "2026-04-02" in dates
