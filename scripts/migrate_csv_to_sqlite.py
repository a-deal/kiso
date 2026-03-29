#!/usr/bin/env python3
"""Migrate per-user CSV/JSON health data into kasane.db SQLite tables.

Usage:
    python3 scripts/migrate_csv_to_sqlite.py                    # migrate all users
    python3 scripts/migrate_csv_to_sqlite.py --user andrew      # single user
    python3 scripts/migrate_csv_to_sqlite.py --user andrew --dry-run  # preview only

CSVs are NOT deleted after migration (kept as backup).
Idempotent: uses INSERT OR IGNORE on unique indexes.
"""

import argparse
import csv
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.gateway.db import get_db, init_db


def _now():
    return datetime.now(timezone.utc).isoformat()


def _deterministic_id(person_id: str, table: str, *parts: str) -> str:
    """Generate a deterministic UUID from person + table + key fields.

    This ensures re-running the migration produces the same IDs,
    making INSERT OR IGNORE work correctly.
    """
    raw = f"{person_id}:{table}:{':'.join(str(p) for p in parts)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def _safe_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def _read_json(path: Path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def migrate_weight(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "weight_log.csv")
    now = _now()
    count = 0
    for r in rows:
        date = r.get("date", "").strip()
        weight = _safe_float(r.get("weight_lbs"))
        if not date or weight is None:
            continue
        rid = _deterministic_id(person_id, "weight_entry", date)
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO weight_entry (id, person_id, date, weight_lbs, waist_in, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, weight, _safe_float(r.get("waist_in")), r.get("source"), now, now),
        )
        count += 1
    return count


def migrate_meals(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "meal_log.csv")
    now = _now()
    count = 0
    for r in rows:
        date = r.get("date", "").strip()
        if not date:
            continue
        meal_num = r.get("meal_num", str(count))
        rid = _deterministic_id(person_id, "meal_entry", date, str(meal_num), r.get("description", "")[:50])
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO meal_entry (id, person_id, date, meal_num, time_of_day, description, "
            "protein_g, carbs_g, fat_g, calories, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, _safe_int(meal_num), r.get("time_of_day"),
             r.get("description"), _safe_float(r.get("protein_g")), _safe_float(r.get("carbs_g")),
             _safe_float(r.get("fat_g")), _safe_float(r.get("calories")), r.get("notes"), now, now),
        )
        count += 1
    return count


def migrate_bp(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "bp_log.csv")
    now = _now()
    count = 0
    for r in rows:
        date = r.get("date", "").strip()
        sys_val = _safe_float(r.get("systolic"))
        dia_val = _safe_float(r.get("diastolic"))
        if not date or sys_val is None or dia_val is None:
            continue
        rid = _deterministic_id(person_id, "bp_entry", date)
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO bp_entry (id, person_id, date, systolic, diastolic, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, sys_val, dia_val, r.get("source"), now, now),
        )
        count += 1
    return count


def migrate_sessions(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "session_log.csv")
    now = _now()
    count = 0
    for r in rows:
        date = r.get("date", "").strip()
        if not date:
            continue
        rid = _deterministic_id(person_id, "training_session", date, r.get("name", ""))
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO training_session (id, person_id, date, rpe, duration_min, type, name, notes, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, _safe_float(r.get("rpe")), _safe_float(r.get("duration_min")),
             r.get("type"), r.get("name"), r.get("notes"), "session_log", now, now),
        )
        count += 1
    return count


def migrate_strength(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "strength_log.csv")
    now = _now()
    count = 0
    for r in rows:
        date = r.get("date", "").strip()
        exercise = r.get("exercise", "").strip()
        if not date or not exercise:
            continue
        rid = _deterministic_id(person_id, "strength_set", date, exercise, str(r.get("weight_lbs", "")), str(count))
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO strength_set (id, person_id, date, exercise, weight_lbs, reps, rpe, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, exercise, _safe_float(r.get("weight_lbs")),
             _safe_int(r.get("reps")), _safe_float(r.get("rpe")), r.get("notes"), now, now),
        )
        count += 1
    return count


def migrate_wearable_daily(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    data = _read_json(data_dir / "garmin_daily.json")
    if not data or not isinstance(data, list):
        return 0
    now = _now()
    count = 0
    for entry in data:
        date = entry.get("date", "").strip()
        if not date:
            continue
        rid = _deterministic_id(person_id, "wearable_daily", date)
        if dry_run:
            count += 1
            continue
        db.execute(
            "INSERT OR REPLACE INTO wearable_daily (id, person_id, date, source, "
            "rhr, hrv, hrv_weekly_avg, hrv_status, steps, sleep_hrs, deep_sleep_hrs, "
            "light_sleep_hrs, rem_sleep_hrs, awake_hrs, sleep_start, sleep_end, "
            "calories_total, calories_active, calories_bmr, stress_avg, floors, "
            "distance_m, max_hr, min_hr, vo2_max, body_battery, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, person_id, date, "garmin",
             _safe_float(entry.get("rhr")), _safe_float(entry.get("hrv")),
             _safe_float(entry.get("hrv_weekly_avg")), entry.get("hrv_status"),
             _safe_int(entry.get("steps")), _safe_float(entry.get("sleep_hrs")),
             _safe_float(entry.get("deep_sleep_hrs")), _safe_float(entry.get("light_sleep_hrs")),
             _safe_float(entry.get("rem_sleep_hrs")), _safe_float(entry.get("awake_hrs")),
             entry.get("sleep_start"), entry.get("sleep_end"),
             _safe_float(entry.get("calories_total")), _safe_float(entry.get("calories_active")),
             _safe_float(entry.get("calories_bmr")), _safe_int(entry.get("stress_avg")),
             _safe_float(entry.get("floors")), _safe_float(entry.get("distance_m")),
             _safe_int(entry.get("max_hr")), _safe_int(entry.get("min_hr")),
             _safe_float(entry.get("vo2_max")), _safe_int(entry.get("body_battery")),
             now, now),
        )
        count += 1
    return count


def migrate_labs(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    data = _read_json(data_dir / "lab_results.json")
    if not data:
        return 0
    now = _now()
    count = 0

    draws = data.get("draws", [])
    latest = data.get("latest", {})

    for draw in draws:
        draw_date = draw.get("date", "").strip()
        if not draw_date:
            continue
        draw_id = _deterministic_id(person_id, "lab_draw", draw_date)
        if not dry_run:
            db.execute(
                "INSERT OR IGNORE INTO lab_draw (id, person_id, date, source, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (draw_id, person_id, draw_date, draw.get("source"), draw.get("notes"), now, now),
            )

        results = draw.get("results", {})
        for marker, value in results.items():
            result_id = _deterministic_id(person_id, "lab_result", draw_date, marker)
            val_float = _safe_float(value)
            val_text = str(value) if val_float is None and value is not None else None
            if not dry_run:
                db.execute(
                    "INSERT OR IGNORE INTO lab_result (id, draw_id, person_id, marker, value, value_text, unit, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (result_id, draw_id, person_id, marker, val_float, val_text, None, now, now),
                )
            count += 1

    # Also insert latest values if no draws exist
    if not draws and latest:
        draw_id = _deterministic_id(person_id, "lab_draw", "latest")
        if not dry_run:
            db.execute(
                "INSERT OR IGNORE INTO lab_draw (id, person_id, date, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (draw_id, person_id, "latest", "imported", now, now),
            )
        for marker, value in latest.items():
            result_id = _deterministic_id(person_id, "lab_result", "latest", marker)
            val_float = _safe_float(value)
            val_text = str(value) if val_float is None and value is not None else None
            if not dry_run:
                db.execute(
                    "INSERT OR IGNORE INTO lab_result (id, draw_id, person_id, marker, value, value_text, unit, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (result_id, draw_id, person_id, marker, val_float, val_text, None, now, now),
                )
            count += 1

    return count


def migrate_habits(db, person_id: str, data_dir: Path, dry_run: bool) -> int:
    rows = _read_csv(data_dir / "daily_habits.csv")
    if not rows:
        return 0
    now = _now()
    count = 0

    # Columns that aren't habits
    skip_cols = {"date", "notes", "_feedback"}

    for r in rows:
        date = r.get("date", "").strip()
        if not date:
            continue
        for col, val in r.items():
            if col in skip_cols or not col.strip():
                continue
            if val is None or val.strip() == "":
                continue
            completed = val.strip().lower() in ("1", "true", "yes", "x", "done")
            rid = _deterministic_id(person_id, "habit_log", date, col)
            if dry_run:
                count += 1
                continue
            db.execute(
                "INSERT OR IGNORE INTO habit_log (id, person_id, date, habit_name, completed, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, person_id, date, col, int(completed), None, now, now),
            )
            count += 1
    return count


def migrate_user(db, person_id: str, data_dir: Path, dry_run: bool):
    """Migrate all CSV/JSON data for one user."""
    label = "DRY RUN" if dry_run else "MIGRATING"
    print(f"\n{label}: {person_id} ({data_dir})")

    results = {
        "weight": migrate_weight(db, person_id, data_dir, dry_run),
        "meals": migrate_meals(db, person_id, data_dir, dry_run),
        "bp": migrate_bp(db, person_id, data_dir, dry_run),
        "sessions": migrate_sessions(db, person_id, data_dir, dry_run),
        "strength": migrate_strength(db, person_id, data_dir, dry_run),
        "wearable": migrate_wearable_daily(db, person_id, data_dir, dry_run),
        "labs": migrate_labs(db, person_id, data_dir, dry_run),
        "habits": migrate_habits(db, person_id, data_dir, dry_run),
    }

    if not dry_run:
        db.commit()

    total = sum(results.values())
    for table, count in results.items():
        if count > 0:
            print(f"  {table}: {count} rows")
    print(f"  TOTAL: {total} rows")
    return results


def main():
    parser = argparse.ArgumentParser(description="Migrate CSV/JSON health data to SQLite")
    parser.add_argument("--user", help="Migrate a single user (health_engine_user_id)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--db", help="Override DB path (for testing)")
    args = parser.parse_args()

    db = get_db(args.db) if args.db else get_db()
    init_db(args.db) if args.db else init_db()

    # Find users: person records with health_engine_user_id set
    if args.user:
        rows = db.execute(
            "SELECT id, health_engine_user_id FROM person WHERE health_engine_user_id = ? AND deleted_at IS NULL",
            (args.user,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, health_engine_user_id FROM person WHERE health_engine_user_id IS NOT NULL AND deleted_at IS NULL"
        ).fetchall()

    if not rows:
        print("No users found with health_engine_user_id set.")
        return

    from mcp_server.tools import PROJECT_ROOT
    users_dir = PROJECT_ROOT / "data" / "users"

    for row in rows:
        person_id = row["id"]
        he_uid = row["health_engine_user_id"]
        data_dir = users_dir / he_uid

        if not data_dir.exists():
            print(f"Skipping {he_uid}: no data directory at {data_dir}")
            continue

        migrate_user(db, person_id, data_dir, args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
