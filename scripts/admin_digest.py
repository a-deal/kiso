#!/usr/bin/env python3
"""
Generate a daily admin digest covering all active users.

Pulls data from kasane.db (habits, check-ins, health measurements, workouts)
and from per-user data directories (garmin_latest.json, weight_log.csv, etc.).

Usage:
    python3 scripts/admin_digest.py                  # print to stdout + save file
    python3 scripts/admin_digest.py --dry-run        # print only, don't save
    python3 scripts/admin_digest.py --telegram        # also post to Telegram
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

try:
    import requests
except ImportError:
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "kasane.db"
DATA_DIR = PROJECT_ROOT / "data"
USERS_YAML = PROJECT_ROOT / "workspace" / "users.yaml"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: Path) -> Optional[sqlite3.Connection]:
    """Read-only connection to kasane.db. Returns None if DB missing."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _file_age_hours(path: Path) -> Optional[float]:
    try:
        mtime = os.path.getmtime(path)
        delta = datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)
        return round(delta.total_seconds() / 3600, 1)
    except FileNotFoundError:
        return None


def _wearable_freshness_sqlite(person_id: str) -> Optional[dict]:
    """Get wearable freshness from wearable_daily SQLite table.

    Returns dict with has_wearable, source, last_date, updated_at.
    Returns None if no data found.
    """
    try:
        from engine.gateway.db import get_db, init_db
        init_db()
        row = get_db().execute(
            "SELECT source, date, updated_at FROM wearable_daily "
            "WHERE person_id = ? ORDER BY date DESC LIMIT 1",
            (person_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "has_wearable": True,
            "source": row["source"],
            "last_date": row["date"],
            "updated_at": row["updated_at"],
        }
    except Exception:
        return None


def _user_data_dir(data_dir: Path, user_id: str) -> Path:
    """Return the data directory for a user.

    Andrew's data lives at data/ (root), everyone else at data/users/<user_id>/.
    """
    if user_id in ("andrew", "default"):
        return data_dir
    return data_dir / "users" / user_id


# ---------------------------------------------------------------------------
# Load user list
# ---------------------------------------------------------------------------

def load_users() -> list[dict]:
    """Load users from workspace/users.yaml, falling back to a hardcoded list."""
    users = []

    if USERS_YAML.exists():
        with open(USERS_YAML) as f:
            data = yaml.safe_load(f) or {}
        seen = set()
        for phone, info in data.get("users", {}).items():
            uid = info.get("user_id", "")
            if uid == "default":
                uid = "andrew"
            if uid in seen:
                continue
            seen.add(uid)
            users.append({
                "user_id": uid,
                "name": info.get("name", uid.title()),
                "phone": phone,
                "role": info.get("role"),
            })
    else:
        users = [
            {"user_id": "andrew", "name": "Andrew", "phone": None, "role": "admin"},
            {"user_id": "paul", "name": "Paul", "phone": None, "role": "admin"},
            {"user_id": "mike", "name": "Mike", "phone": None, "role": None},
            {"user_id": "dad", "name": "Dad", "phone": None, "role": None},
            {"user_id": "grigoriy", "name": "Grigoriy", "phone": None, "role": None},
        ]

    return users


# ---------------------------------------------------------------------------
# Per-user data gathering
# ---------------------------------------------------------------------------

def gather_user_data(user: dict, conn: Optional[sqlite3.Connection], data_dir: Path) -> dict:
    """Collect last-24h stats for one user."""
    uid = user["user_id"]
    udir = _user_data_dir(data_dir, uid)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    result = {
        "user_id": uid,
        "name": user["name"],
        "has_data_dir": udir.is_dir(),
        "habits_yesterday": None,
        "habits_total": None,
        "streak_days": 0,
        "weight_current": None,
        "weight_avg": None,
        "bp_latest": None,
        "workout_count_7d": 0,
        "garmin_synced_ago": None,
        "garmin_age_hours": None,
        "has_wearable": False,
        "day_number": None,
        "last_activity_date": None,
        "signals": [],
    }

    if not udir.is_dir() and uid not in ("andrew", "default"):
        result["signals"].append("no data directory")
        return result

    # DB queries
    if conn:
        _gather_from_db(result, conn, uid, today, yesterday, now)

    # File-based data
    _gather_from_files(result, udir, now)

    # Coaching signals
    _compute_signals(result, now)

    return result


def _gather_from_db(
    result: dict,
    conn: sqlite3.Connection,
    uid: str,
    today: str,
    yesterday: str,
    now: datetime,
):
    """Pull habit/checkin/health/workout data from kasane.db."""
    person_ids = []

    # Match by health_engine_user_id
    rows = conn.execute(
        "SELECT id FROM person WHERE health_engine_user_id = ? AND deleted_at IS NULL",
        (uid,),
    ).fetchall()
    person_ids.extend(r["id"] for r in rows)

    # Match by name (case-insensitive) as fallback
    if not person_ids:
        rows = conn.execute(
            "SELECT id FROM person WHERE LOWER(name) = LOWER(?) AND deleted_at IS NULL",
            (result["name"],),
        ).fetchall()
        person_ids.extend(r["id"] for r in rows)

    # Check if habits exist directly under uid as person_id
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM habit WHERE person_id = ? AND deleted_at IS NULL",
        (uid,),
    ).fetchone()
    if row and row["cnt"] > 0:
        person_ids.append(uid)

    person_ids = list(set(person_ids))
    if not person_ids:
        return

    placeholders = ",".join("?" for _ in person_ids)

    # Active habits
    habits = conn.execute(
        f"SELECT id, title FROM habit WHERE person_id IN ({placeholders}) "
        f"AND state = 'active' AND deleted_at IS NULL",
        person_ids,
    ).fetchall()
    result["habits_total"] = len(habits)

    if habits:
        habit_ids = [h["id"] for h in habits]
        h_placeholders = ",".join("?" for _ in habit_ids)

        # Yesterday's completions
        completed = conn.execute(
            f"SELECT COUNT(*) as cnt FROM check_in "
            f"WHERE habit_id IN ({h_placeholders}) AND date = ? AND completed = 1 "
            f"AND deleted_at IS NULL",
            habit_ids + [yesterday],
        ).fetchone()
        result["habits_yesterday"] = f"{completed['cnt']}/{len(habits)}" if completed else None

        # Streak: consecutive days with at least one check-in, counting back from yesterday
        streak = 0
        check_date = now - timedelta(days=1)
        for _ in range(60):
            d = check_date.strftime("%Y-%m-%d")
            day_check = conn.execute(
                f"SELECT COUNT(*) as cnt FROM check_in "
                f"WHERE habit_id IN ({h_placeholders}) AND date = ? AND completed = 1 "
                f"AND deleted_at IS NULL",
                habit_ids + [d],
            ).fetchone()
            if day_check and day_check["cnt"] > 0:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break
        result["streak_days"] = streak

        # First check-in date (for day number)
        first = conn.execute(
            f"SELECT MIN(date) as d FROM check_in "
            f"WHERE habit_id IN ({h_placeholders}) AND deleted_at IS NULL",
            habit_ids,
        ).fetchone()
        if first and first["d"]:
            try:
                first_date = datetime.strptime(first["d"], "%Y-%m-%d")
                result["day_number"] = (now - first_date).days + 1
            except ValueError:
                pass

        # Last activity date
        last = conn.execute(
            f"SELECT MAX(date) as d FROM check_in "
            f"WHERE habit_id IN ({h_placeholders}) AND completed = 1 AND deleted_at IS NULL",
            habit_ids,
        ).fetchone()
        if last and last["d"]:
            result["last_activity_date"] = last["d"]

    # Health measurements (weight, BP) from last 7 days
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    measurements = conn.execute(
        f"SELECT type_identifier, value, unit, date FROM health_measurement "
        f"WHERE person_id IN ({placeholders}) AND deleted_at IS NULL AND date >= ? "
        f"ORDER BY date DESC",
        person_ids + [week_ago],
    ).fetchall()

    for m in measurements:
        if m["type_identifier"] in ("bodyMass", "weight") and result["weight_current"] is None:
            result["weight_current"] = round(m["value"], 1)
        if m["type_identifier"] == "bloodPressureSystolic" and result["bp_latest"] is None:
            dia = conn.execute(
                f"SELECT value FROM health_measurement "
                f"WHERE person_id IN ({placeholders}) "
                f"AND type_identifier = 'bloodPressureDiastolic' "
                f"AND date = ? AND deleted_at IS NULL LIMIT 1",
                person_ids + [m["date"]],
            ).fetchone()
            if dia:
                result["bp_latest"] = f"{int(m['value'])}/{int(dia['value'])}"

    # Workouts in last 7 days
    workouts = conn.execute(
        f"SELECT COUNT(*) as cnt FROM workout_record "
        f"WHERE person_id IN ({placeholders}) AND date >= ? AND deleted_at IS NULL",
        person_ids + [week_ago],
    ).fetchone()
    if workouts:
        result["workout_count_7d"] = workouts["cnt"]


def _gather_from_files(result: dict, udir: Path, now: datetime):
    """Pull data from CSV/JSON files in the user data directory."""
    # Wearable freshness: SQLite first, JSON fallback
    uid = result.get("user_id")
    pid = None
    if uid:
        try:
            from engine.gateway.db import get_db, init_db
            init_db()
            row = get_db().execute(
                "SELECT id FROM person WHERE health_engine_user_id = ? AND deleted_at IS NULL",
                (uid,),
            ).fetchone()
            if row:
                pid = row["id"]
        except Exception:
            pass

    freshness = _wearable_freshness_sqlite(pid) if pid else None
    if freshness:
        result["has_wearable"] = True
        # Compute age from updated_at
        try:
            ts = datetime.fromisoformat(freshness["updated_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            result["garmin_age_hours"] = round(age, 1)
            if age < 1:
                result["garmin_synced_ago"] = "synced <1h ago"
            elif age < 24:
                result["garmin_synced_ago"] = f"synced {int(age)}h ago"
            else:
                result["garmin_synced_ago"] = f"synced {int(age / 24)}d ago"
        except Exception:
            pass
    else:
        # JSON fallback
        garmin_path = udir / "garmin_latest.json"
        garmin = _read_json(garmin_path)
        if garmin:
            result["has_wearable"] = True
            age = _file_age_hours(garmin_path)
            result["garmin_age_hours"] = age
            if age is not None:
                if age < 1:
                    result["garmin_synced_ago"] = "synced <1h ago"
                elif age < 24:
                    result["garmin_synced_ago"] = f"synced {int(age)}h ago"
                else:
                    result["garmin_synced_ago"] = f"synced {int(age / 24)}d ago"

    # Weight from CSV (more complete than DB for some users)
    weight_csv = udir / "weight_log.csv"
    if weight_csv.exists() and result["weight_current"] is None:
        try:
            with open(weight_csv) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if rows:
                last = rows[-1]
                w = last.get("weight_lbs") or last.get("weight")
                if w:
                    result["weight_current"] = round(float(w), 1)
                # 7-day rolling average
                recent = rows[-7:]
                vals = []
                for r in recent:
                    v = r.get("weight_lbs") or r.get("weight")
                    if v:
                        vals.append(float(v))
                if vals:
                    result["weight_avg"] = round(sum(vals) / len(vals), 1)
        except (ValueError, KeyError):
            pass

    # Weight avg from CSV even if DB had the current weight
    if weight_csv.exists() and result["weight_avg"] is None:
        try:
            with open(weight_csv) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            recent = rows[-7:]
            vals = []
            for r in recent:
                v = r.get("weight_lbs") or r.get("weight")
                if v:
                    vals.append(float(v))
            if vals:
                result["weight_avg"] = round(sum(vals) / len(vals), 1)
        except (ValueError, KeyError):
            pass

    # Briefing as fallback for weight
    briefing = _read_json(udir / "briefing.json")
    if briefing and result["weight_current"] is None:
        w_section = briefing.get("weight", {})
        if isinstance(w_section, dict):
            result["weight_current"] = w_section.get("current")
            if result["weight_avg"] is None:
                result["weight_avg"] = w_section.get("rolling_avg_7d")


def _compute_signals(result: dict, now: datetime):
    """Flag coaching signals."""
    signals = result["signals"]

    # New user (first 7 days)
    if result["day_number"] is not None and result["day_number"] <= 7:
        signals.append(f"new user (Day {result['day_number']})")

    # Streak broken: yesterday was 0 completions
    if result["habits_yesterday"] and result["habits_yesterday"].startswith("0/"):
        if result["streak_days"] == 0:
            signals.append("streak broken")

    # Quiet > 48h
    if result["last_activity_date"]:
        try:
            last = datetime.strptime(result["last_activity_date"], "%Y-%m-%d")
            hours_since = (now - last).total_seconds() / 3600
            if hours_since > 48:
                days = int(hours_since / 24)
                signals.append(f"quiet {days}d")
        except ValueError:
            pass

    # No wearable
    if not result["has_wearable"]:
        signals.append("no wearable")

    # Garmin stale (>24h)
    if result["garmin_age_hours"] and result["garmin_age_hours"] > 24:
        signals.append("garmin stale")


# ---------------------------------------------------------------------------
# Format the digest
# ---------------------------------------------------------------------------

def format_digest(users_data: list[dict]) -> str:
    """Format all user data into a compact plaintext digest."""
    now = datetime.now()
    date_str = now.strftime("%b %d")

    lines = [f"BASELINE DAILY DIGEST -- {date_str}", ""]

    needs_attention = 0
    shown = 0

    for u in users_data:
        # Skip users with zero data
        if (
            not u["has_data_dir"]
            and u["user_id"] not in ("andrew", "default")
            and not u["habits_total"]
            and not u["day_number"]
        ):
            continue

        shown += 1

        # Status emoji
        has_issues = bool(u["signals"])
        if not has_issues and u["habits_total"]:
            status_icon = "[ok]"
        elif has_issues:
            status_icon = "[!]"
            needs_attention += 1
        else:
            status_icon = "[-]"

        # Header
        day_str = f" (Day {u['day_number']})" if u["day_number"] else ""
        lines.append(f"{u['name'].upper()}{day_str} {status_icon}")

        # Habits
        if u["habits_yesterday"] is not None:
            streak_str = ""
            if u["streak_days"] > 0:
                streak_str = f" / {u['streak_days']}-day streak"
            lines.append(f"  Habits: {u['habits_yesterday']} yesterday{streak_str}")
        elif u["habits_total"] and u["habits_total"] > 0:
            lines.append(f"  Habits: {u['habits_total']} tracked, no check-ins yesterday")
        else:
            lines.append("  Habits: none tracked")

        # Weight
        if u["weight_current"]:
            avg_str = f" (avg {u['weight_avg']})" if u["weight_avg"] else ""
            lines.append(f"  Weight: {u['weight_current']} lbs{avg_str}")

        # BP
        if u["bp_latest"]:
            lines.append(f"  BP: {u['bp_latest']}")

        # Workouts
        if u["workout_count_7d"] > 0:
            lines.append(f"  Workouts: {u['workout_count_7d']} this week")

        # Garmin
        if u["has_wearable"]:
            lines.append(f"  Garmin: {u['garmin_synced_ago']}")
        elif "no wearable" in u["signals"]:
            lines.append("  No wearable connected")

        # Extra signals
        extra_signals = [s for s in u["signals"] if s != "no wearable"]
        if extra_signals:
            lines.append(f"  Flags: {', '.join(extra_signals)}")

        lines.append("")

    # Footer
    lines.append("---")
    attn_str = f" / {needs_attention} needs attention" if needs_attention else ""
    lines.append(f"{shown} active users{attn_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def save_digest(text: str, data_dir: Path) -> Path:
    """Save digest to data/admin/daily_digest.txt."""
    out_dir = data_dir / "admin"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "daily_digest.txt"
    with open(out_path, "w") as f:
        f.write(text)
    return out_path


def send_telegram(text: str) -> bool:
    """Post digest to Telegram via Bot API."""
    if requests is None:
        print("ERROR: 'requests' package not installed. Run: pip install requests", file=sys.stderr)
        return False

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("Telegram: sent successfully.", file=sys.stderr)
            return True
        else:
            print(f"Telegram error {resp.status_code}: {resp.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Telegram request failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate daily admin digest")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout only, don't save")
    parser.add_argument("--telegram", action="store_true", help="Also post to Telegram")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="Override data directory",
    )
    args = parser.parse_args()

    data_dir = DATA_DIR
    db_path = DB_PATH
    if args.data_dir:
        data_dir = args.data_dir.resolve()
        db_path = data_dir / "kasane.db"

    # Load users
    users = load_users()
    if not users:
        print("No users found.", file=sys.stderr)
        sys.exit(1)

    # Open DB
    conn = _get_db(db_path)

    # Gather data for each user
    users_data = []
    for user in users:
        data = gather_user_data(user, conn, data_dir)
        users_data.append(data)

    if conn:
        conn.close()

    # Format
    digest = format_digest(users_data)

    # Output
    print(digest)

    if not args.dry_run:
        out_path = save_digest(digest, data_dir)
        print(f"\nSaved to {out_path}", file=sys.stderr)

    if args.telegram:
        send_telegram(digest)


if __name__ == "__main__":
    main()
