"""Tests for scripts/health_deep_alert_parser.py.

The parser reads /health/deep JSON and emits alert lines for users whose
stale_critical_files field is non-empty. Loaded via importlib because
scripts/ is not a package.
"""

import importlib.util
from pathlib import Path

import pytest

PARSER_PATH = Path(__file__).parent.parent / "scripts" / "health_deep_alert_parser.py"


@pytest.fixture(scope="module")
def parser():
    spec = importlib.util.spec_from_file_location("health_deep_alert_parser", PARSER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empty_response_no_alerts(parser):
    assert parser.format_stale_critical_alerts({}) == []


def test_no_user_data_no_alerts(parser):
    assert parser.format_stale_critical_alerts({"checks": {}}) == []


def test_fresh_users_no_alerts(parser):
    resp = {
        "checks": {
            "user_data": {
                "andrew": {"status": "ok", "last_data_hours_ago": 1.0},
                "grigoriy": {"status": "ok", "last_data_hours_ago": 2.0},
            }
        }
    }
    assert parser.format_stale_critical_alerts(resp) == []


def test_stale_user_without_stale_critical_files_no_alert(parser):
    """User is stale by aggregate but no critical files flagged — not our alert.

    The aggregate-only stale case is covered by a different alert path (or none).
    This parser specifically targets the per-file tripwire.
    """
    resp = {
        "checks": {
            "user_data": {
                "dad": {"status": "stale", "last_data_hours_ago": 212.8},
            }
        }
    }
    assert parser.format_stale_critical_alerts(resp) == []


def test_stale_critical_file_emits_alert(parser):
    resp = {
        "checks": {
            "user_data": {
                "andrew": {
                    "status": "stale",
                    "last_data_hours_ago": 1.1,
                    "stale_critical_files": [
                        {"file": "apple_health_latest.json", "age_hours": 200.5}
                    ],
                }
            }
        }
    }
    alerts = parser.format_stale_critical_alerts(resp)
    assert len(alerts) == 1
    assert "andrew" in alerts[0]
    assert "apple_health_latest.json" in alerts[0]
    assert "200.5" in alerts[0]


def test_multiple_stale_files_single_user(parser):
    resp = {
        "checks": {
            "user_data": {
                "andrew": {
                    "status": "stale",
                    "stale_critical_files": [
                        {"file": "apple_health_latest.json", "age_hours": 200.0},
                        {"file": "garmin_latest.json", "age_hours": 150.0},
                    ],
                }
            }
        }
    }
    alerts = parser.format_stale_critical_alerts(resp)
    assert len(alerts) == 1
    assert "apple_health_latest.json" in alerts[0]
    assert "garmin_latest.json" in alerts[0]


def test_multiple_users_each_get_alert(parser):
    resp = {
        "checks": {
            "user_data": {
                "andrew": {
                    "status": "stale",
                    "stale_critical_files": [
                        {"file": "apple_health_latest.json", "age_hours": 200.0}
                    ],
                },
                "paul": {
                    "status": "stale",
                    "stale_critical_files": [
                        {"file": "garmin_latest.json", "age_hours": 180.0}
                    ],
                },
            }
        }
    }
    alerts = parser.format_stale_critical_alerts(resp)
    assert len(alerts) == 2
    uids = " ".join(alerts)
    assert "andrew" in uids
    assert "paul" in uids


def test_malformed_entry_skipped(parser):
    """Non-dict user entries should be skipped gracefully, not crash."""
    resp = {
        "checks": {
            "user_data": {
                "andrew": "not a dict",
                "paul": {
                    "status": "stale",
                    "stale_critical_files": [
                        {"file": "garmin_latest.json", "age_hours": 180.0}
                    ],
                },
            }
        }
    }
    alerts = parser.format_stale_critical_alerts(resp)
    assert len(alerts) == 1
    assert "paul" in alerts[0]
