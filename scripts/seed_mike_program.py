#!/usr/bin/env python3
"""Seed Mike's April 2026 workout program into kasane.db.

Run from project root:
    python3 scripts/seed_mike_program.py
"""
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.gateway.db import get_db, init_db


def make_id(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, ":".join(str(p) for p in parts)))


def main():
    init_db()
    db = get_db()
    now = datetime.now().isoformat()

    # Resolve Mike's person_id
    row = db.execute(
        "SELECT id FROM person WHERE health_engine_user_id = 'mike' AND deleted_at IS NULL"
    ).fetchone()
    if not row:
        print("ERROR: No person record found for user_id='mike'")
        print("Run seed_users.py first or create Mike's person record.")
        sys.exit(1)

    person_id = row["id"]
    print(f"Found Mike: person_id={person_id}")

    # Check for existing active program
    existing = db.execute(
        "SELECT id, name FROM workout_program WHERE person_id = ? AND status = 'active'",
        (person_id,),
    ).fetchone()
    if existing:
        print(f"Deactivating existing program: {existing['name']} ({existing['id']})")
        db.execute(
            "UPDATE workout_program SET status = 'replaced', updated_at = ? WHERE id = ?",
            (now, existing["id"]),
        )

    # Create program
    program_id = make_id("mike", "program", "return-to-strength-april-2026")
    db.execute(
        "INSERT OR REPLACE INTO workout_program (id, person_id, name, description, days_per_week, "
        "start_date, end_date, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            program_id, person_id,
            "Return to Strength - April 2026",
            "Concurrent strength and conditioning. 3 gym + 1 home. Conditioning bias weeks 1-2.",
            4, "2026-04-01", "2026-05-04", "active", now, now,
        ),
    )
    print(f"Created program: {program_id}")

    # Day templates
    days = [
        {
            "day_number": 1,
            "name": "Upper Push",
            "day_type": "gym",
            "notes": "Finish with 8 min Airdyne or rower, easy pace",
            "exercises": [
                ("Bench Press", 4, "5", 7.0, "compound", None),
                ("OHP or DB Shoulder Press", 3, "8", 7.0, "compound", None),
                ("Barbell Row", 3, "8", 7.0, "compound", None),
                ("Tricep Pushdown", 2, "12", None, "accessory", None),
                ("Lateral Raise", 2, "15", None, "accessory", None),
            ],
        },
        {
            "day_number": 2,
            "name": "Lower",
            "day_type": "gym",
            "notes": "Finish with 8 min rowing intervals (30s on / 90s off)",
            "exercises": [
                ("Back Squat", 4, "5", 7.0, "compound", None),
                ("Romanian Deadlift", 3, "8", 7.0, "compound", None),
                ("Leg Press or BSS", 3, "10", None, "compound", None),
                ("Leg Curl", 2, "12", None, "accessory", None),
                ("Calf Raise", 2, "15", None, "accessory", None),
            ],
        },
        {
            "day_number": 3,
            "name": "Full Body Pull",
            "day_type": "gym",
            "notes": "Finish with 8 min Airdyne, moderate effort",
            "exercises": [
                ("Deadlift", 3, "4", 7.0, "compound", None),
                ("Weighted Chin-up or Lat Pulldown", 3, "8", 7.0, "compound", None),
                ("Incline DB Press", 3, "10", None, "compound", None),
                ("Lunges or Step-ups", 2, "10/leg", None, "compound", None),
                ("Barbell Curl", 2, "12", None, "accessory", None),
            ],
        },
        {
            "day_number": 4,
            "name": "Home Conditioning",
            "day_type": "home",
            "notes": "30-40 min total. Recovery and conditioning, not a grind.",
            "exercises": [
                ("Airdyne or Rower Intervals", 6, "30s/90s", None, "conditioning", "6-8 rounds"),
                ("DB Goblet Squat", 3, "15", None, "compound", "20 lb"),
                ("Push-ups", 3, "max", None, "compound", None),
                ("DB Row", 3, "12/arm", None, "compound", "20 lb"),
                ("Plank", 3, "30-45s", None, "accessory", None),
            ],
        },
    ]

    for day_data in days:
        day_id = make_id(program_id, "day", day_data["day_number"])
        db.execute(
            "INSERT OR REPLACE INTO program_day (id, program_id, day_number, name, day_type, notes, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day_id, program_id, day_data["day_number"], day_data["name"],
             day_data["day_type"], day_data["notes"], day_data["day_number"]),
        )
        print(f"  Day {day_data['day_number']}: {day_data['name']} ({day_id})")

        for i, (ex_name, sets, reps, rpe_target, category, notes) in enumerate(day_data["exercises"]):
            ex_id = make_id(day_id, "exercise", i)
            db.execute(
                "INSERT OR REPLACE INTO prescribed_exercise "
                "(id, program_day_id, exercise_name, sets, reps, rpe_target, rest_seconds, notes, sort_order, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ex_id, day_id, ex_name, sets, reps, rpe_target, None, notes, i + 1, category),
            )
            print(f"    {ex_name} {sets}x{reps}" + (f" RPE {rpe_target}" if rpe_target else ""))

    db.commit()
    print(f"\nDone. Program seeded with {sum(len(d['exercises']) for d in days)} exercises across {len(days)} days.")
    print(f"Program URL: andrewdeal.info/guides/mike-program")


if __name__ == "__main__":
    main()
