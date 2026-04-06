#!/usr/bin/env python3
"""Seed person table with channel/contact info from users.yaml.

One-time migration: reads the existing users.yaml and updates the person
table with phone, email, channel, channel_target, timezone, role.

Safe to run multiple times (updates only, never deletes).

WARNING: Run on Mac Mini (production) only. Do not run on laptop.
There is no local development database — tests use tmp_path fixtures.

Usage (on Mac Mini):
    python3 scripts/seed_users.py
    python3 scripts/seed_users.py --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from engine.gateway.db import get_db, init_db

USERS_YAML = Path.home() / ".openclaw" / "workspace" / "users.yaml"

# Channel info not in users.yaml, must be provided here.
# Grigoriy: Telegram (80135247), everyone else: WhatsApp via phone.
CHANNEL_OVERRIDES = {
    "grigoriy": {"channel": "telegram", "channel_target": "80135247"},
    "andrew": {"channel": "whatsapp", "channel_target": "+14152009584"},
    "paul": {"channel": "whatsapp", "channel_target": "+17038878948"},
    "mike": {"channel": "whatsapp", "channel_target": "+17033625977"},
    "dad": {"channel": "whatsapp", "channel_target": "+12022552119"},
    "dean": {"channel": "whatsapp", "channel_target": "+16509636822"},
    "yusuf": {"channel": "whatsapp", "channel_target": "+15105018493"},
    "manny": {"channel": "whatsapp", "channel_target": "+19255426289"},
}

TIMEZONE_OVERRIDES = {
    "grigoriy": "Europe/Moscow",
}

ROLE_OVERRIDES = {
    "andrew": "admin",
    "paul": "admin",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not USERS_YAML.exists():
        print(f"users.yaml not found at {USERS_YAML}")
        sys.exit(1)

    with open(USERS_YAML) as f:
        data = yaml.safe_load(f) or {}

    init_db()
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    for phone, info in data.get("users", {}).items():
        user_id = info.get("user_id", "")
        name = info.get("name", "")
        email = info.get("email", "")
        role = ROLE_OVERRIDES.get(user_id, info.get("role", "user"))
        tz = TIMEZONE_OVERRIDES.get(user_id, "America/Los_Angeles")
        ch = CHANNEL_OVERRIDES.get(user_id, {})
        channel = ch.get("channel", "")
        channel_target = ch.get("channel_target", "")

        row = db.execute(
            "SELECT id FROM person WHERE health_engine_user_id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()

        if row:
            print(f"  UPDATE {user_id}: phone={phone}, channel={channel}, tz={tz}")
            if not args.dry_run:
                db.execute(
                    """UPDATE person SET phone = ?, email = ?, channel = ?,
                       channel_target = ?, timezone = ?, role = ?, updated_at = ?
                       WHERE id = ?""",
                    (phone, email, channel, channel_target, tz, role, now, row["id"]),
                )
        else:
            pid = f"{user_id}-001"
            print(f"  INSERT {user_id}: phone={phone}, channel={channel}, tz={tz}")
            if not args.dry_run:
                db.execute(
                    """INSERT INTO person (id, name, health_engine_user_id, phone, email,
                       channel, channel_target, timezone, role, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, name, user_id, phone, email, channel, channel_target, tz, role, now, now),
                )

    if not args.dry_run:
        db.commit()
        print("\nSeeded. Verify with: sqlite3 data/kasane.db \"SELECT health_engine_user_id, phone, channel, channel_target, timezone FROM person WHERE deleted_at IS NULL\"")
    else:
        print("\n(dry run, no changes made)")


if __name__ == "__main__":
    main()
