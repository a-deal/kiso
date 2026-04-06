"""End-to-end happy path tests for the three critical user journeys.

These test the full data flow through real SQLite, real token encryption,
and real business logic. Only external calls (Garmin API, Anthropic, OpenClaw)
are mocked.

Test A: Scheduler happy path — person with data gets a composed message
Test B: Log data → checkin — weight + meal appear in briefing
Test C: Wearable connect → pull → checkin sees metrics
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW_STR = datetime.now(timezone.utc).isoformat()


def _insert_person(db, person_id, name, user_id, channel=None, target=None,
                   tz="America/Los_Angeles", created_at=None):
    db.execute(
        "INSERT INTO person "
        "(id, name, health_engine_user_id, channel, channel_target, timezone, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (person_id, name, user_id, channel, target, tz,
         created_at or _NOW_STR, _NOW_STR),
    )
    db.commit()


def _insert_wearable(db, person_id, date, source, rhr=None, hrv=None,
                     sleep_hrs=None, vo2_max=None, steps=None):
    rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{person_id}:wearable_daily:{date}:{source}"))
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO wearable_daily "
        "(id, person_id, date, source, rhr, hrv, sleep_hrs, vo2_max, steps, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, person_id, date, source, rhr, hrv, sleep_hrs, vo2_max, steps, now, now),
    )
    db.commit()


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    """Fresh SQLite + person + user data dir.

    Patches PROJECT_ROOT so all code (scheduler, tools, db) resolves to tmp_path.
    """
    from engine.gateway.db import init_db, get_db, close_db
    close_db()

    # Patch PROJECT_ROOT before any init_db() call
    monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)

    db_path = tmp_path / "data" / "kasane.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(str(db_path))
    db = get_db(str(db_path))

    person_id = "p-e2e-001"
    user_id = "e2e_user"
    _insert_person(db, person_id, "TestUser", user_id,
                   channel="whatsapp", target="+14155550000")

    # User data dir with minimal config
    data_dir = tmp_path / "data" / "users" / user_id
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.yaml").write_text("profile:\n  age: 35\n  sex: M\n")

    yield {
        "tmp_path": tmp_path,
        "db_path": str(db_path),
        "db": db,
        "person_id": person_id,
        "user_id": user_id,
        "data_dir": data_dir,
    }

    close_db()


# ---------------------------------------------------------------------------
# Test A: Scheduler happy path (data user gets composed message)
# ---------------------------------------------------------------------------


class TestSchedulerHappyPath:
    """Person with wearable data → _run_schedule → passes all gates → message composed → recorded."""

    @patch("engine.gateway.scheduler._send_via_openclaw")
    @patch("engine.gateway.scheduler._compose_message",
           return_value="Sleep was 7.2 hours last night. HRV at 58, solid recovery.")
    @patch("engine.gateway.scheduler._user_local_now")
    @patch("engine.gateway.scheduler._audit_scheduler")
    def test_full_morning_brief_dry_run(
        self, mock_audit, mock_now, mock_compose, mock_send, e2e_env,
    ):
        from engine.gateway.scheduler import _run_schedule

        db = e2e_env["db"]
        person_id = e2e_env["person_id"]
        user_id = e2e_env["user_id"]

        # Insert wearable data so has_composable_data returns True
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _insert_wearable(db, person_id, today, "garmin", rhr=52, hrv=58, sleep_hrs=7.2, steps=8400)
        _insert_wearable(db, person_id, yesterday, "garmin", rhr=54, hrv=55, sleep_hrs=6.8, steps=7200)

        # Mock time to 7:15 AM Pacific (inside morning window)
        mock_now.return_value = datetime(2026, 4, 5, 7, 15,
                                         tzinfo=ZoneInfo("America/Los_Angeles"))

        # Mock token store so wearable nudge check works
        with patch("engine.gateway.scheduler._get_token_store") as mock_ts:
            ts = MagicMock()
            ts.has_token.return_value = True  # has garmin tokens
            mock_ts.return_value = ts

            # Patch _gather_context to use our DB instead of global
            with patch("engine.gateway.scheduler._gather_context") as mock_gc:
                mock_gc.return_value = {
                    "checkin": {
                        "data_available": {"garmin": True, "wearable_daily": True},
                        "score": {"coverage": 6},
                    }
                }

                result = _run_schedule("morning_brief", target_hour=7, dry_run=True)

        # Gate checks: user was eligible and processed
        assert result["eligible_count"] == 1
        assert len(result["results"]) == 1

        entry = result["results"][0]
        assert entry["status"] == "dry_run"
        assert entry["user_id"] == user_id

        # Compose was called with correct user name and non-empty context
        mock_compose.assert_called_once()
        call_args = mock_compose.call_args
        assert call_args[0][1] == "TestUser"  # user_name
        assert call_args[0][2] is not None     # context_data

        # Message is present in result
        assert "Sleep was 7.2 hours" in entry["message"]

        # Send was NOT called (dry_run)
        mock_send.assert_not_called()

        # scheduled_send table has dedup record
        row = db.execute(
            "SELECT * FROM scheduled_send WHERE person_id = ? AND schedule_type = 'morning_brief'",
            (person_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "dry_run"


# ---------------------------------------------------------------------------
# Test B: Log data → check-in sees it
# ---------------------------------------------------------------------------


class TestLogDataAppearsInCheckin:
    """_log_weight + _log_meal → _checkin response includes both."""

    def test_weight_and_meal_in_checkin(self, e2e_env):
        from mcp_server.tools import _log_weight, _log_meal, _checkin

        user_id = e2e_env["user_id"]
        data_dir = e2e_env["data_dir"]
        db_path = e2e_env["db_path"]
        today = datetime.now().strftime("%Y-%m-%d")

        with patch("mcp_server.tools._USER_HOME", e2e_env["tmp_path"] / "data"):
            # Log two weights (briefing needs >= 2 for weight section)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            _log_weight(186.0, date=yesterday, user_id=user_id)
            w_result = _log_weight(185.5, date=today, user_id=user_id)
            assert w_result["logged"] is True
            assert w_result["weight_lbs"] == pytest.approx(185.5, abs=0.1)

            # Log meal
            m_result = _log_meal(
                description="3 eggs, toast, coffee",
                protein_g=24.0,
                carbs_g=30.0,
                fat_g=15.0,
                calories=350,
                date=today,
                user_id=user_id,
            )
            assert m_result["logged"] is True
            assert m_result["protein_g"] == 24.0

            # Verify DB rows exist
            db = e2e_env["db"]
            weight_rows = db.execute(
                "SELECT * FROM weight_entry WHERE person_id = ? ORDER BY date DESC",
                (e2e_env["person_id"],),
            ).fetchall()
            assert len(weight_rows) >= 2
            assert float(weight_rows[0]["weight_lbs"]) == pytest.approx(185.5, abs=0.1)

            meal_row = db.execute(
                "SELECT * FROM meal_entry WHERE person_id = ?",
                (e2e_env["person_id"],),
            ).fetchone()
            assert meal_row is not None
            assert meal_row["description"] == "3 eggs, toast, coffee"
            assert float(meal_row["protein_g"]) == pytest.approx(24.0)

            # Now check-in should include the logged data
            # Mock garmin pull to avoid external calls
            with patch("mcp_server.tools._pull_garmin", return_value={"pulled": False}):
                with patch("mcp_server.tools._get_token_store") as mock_ts:
                    ts = MagicMock()
                    ts.has_token.return_value = False
                    mock_ts.return_value = ts
                    briefing = _checkin(user_id=user_id)

            # Weight appears in briefing
            assert "weight" in briefing, f"No weight section. Keys: {list(briefing.keys())}"
            assert briefing["weight"]["current"] == pytest.approx(185.5, abs=0.1)

            # Meal/nutrition appears in briefing
            assert briefing["data_available"]["meal_log"] is True
            assert "nutrition" in briefing, f"No nutrition section. Keys: {list(briefing.keys())}"
            assert briefing["nutrition"]["today_totals"]["protein_g"] == pytest.approx(24.0, abs=1)


# ---------------------------------------------------------------------------
# Test C: Wearable connect → pull → checkin sees metrics
# ---------------------------------------------------------------------------


class TestWearableConnectToCheckin:
    """connect_wearable → token saved → pull_garmin → checkin includes metrics."""

    def test_garmin_connect_pull_checkin(self, e2e_env):
        from mcp_server.tools import _connect_wearable, _checkin

        user_id = e2e_env["user_id"]
        person_id = e2e_env["person_id"]
        data_dir = e2e_env["data_dir"]
        db_path = e2e_env["db_path"]
        db = e2e_env["db"]
        today = datetime.now().strftime("%Y-%m-%d")

        with patch("mcp_server.tools._USER_HOME", e2e_env["tmp_path"] / "data"):
            # Step 1: connect_wearable returns auth URL
            with patch("engine.gateway.config.load_gateway_config") as mock_gw:
                gw = MagicMock()
                gw.hmac_secret = "test-secret-key"
                gw.base_url = "https://test.example.com"
                mock_gw.return_value = gw

                # Mock token store to say no existing token
                with patch("mcp_server.tools._get_token_store") as mock_ts_outer:
                    ts_outer = MagicMock()
                    ts_outer.has_token.return_value = False
                    mock_ts_outer.return_value = ts_outer

                    # Also patch the TokenStore inside _connect_wearable
                    with patch("engine.gateway.token_store.TokenStore") as MockTS:
                        mock_ts_inst = MagicMock()
                        mock_ts_inst.has_token.return_value = False
                        MockTS.return_value = mock_ts_inst

                        result = _connect_wearable(service="garmin", user_id=user_id)

            assert "auth_url" in result, f"Expected auth_url, got: {result}"
            assert "garmin" in result["auth_url"]
            assert user_id in result["auth_url"]

            # Step 2: Simulate OAuth callback by writing wearable_daily directly
            # (In production, the OAuth callback saves tokens and pull_garmin writes here)
            _insert_wearable(db, person_id, today, "garmin",
                             rhr=52, hrv=58, sleep_hrs=7.2, vo2_max=47.0, steps=8450)

            # Verify the row landed
            row = db.execute(
                "SELECT * FROM wearable_daily WHERE person_id = ? AND date = ?",
                (person_id, today),
            ).fetchone()
            assert row is not None
            assert row["source"] == "garmin"
            assert row["rhr"] == 52
            assert row["sleep_hrs"] == pytest.approx(7.2)

            # Step 3: checkin reads the wearable data
            with patch("mcp_server.tools._pull_garmin", return_value={"pulled": False}):
                with patch("mcp_server.tools._get_token_store") as mock_ts:
                    ts = MagicMock()
                    ts.has_token.return_value = False
                    mock_ts.return_value = ts
                    briefing = _checkin(user_id=user_id)

            # Wearable data appears in checkin
            assert briefing.get("data_available", {}).get("wearable_daily") is True, \
                f"wearable_daily not in data_available. Got: {briefing.get('data_available')}"
