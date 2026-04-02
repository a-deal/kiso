#!/Users/andrew/src/health-engine/.venv/bin/python3
"""
Baseline Digest — generates and sends a morning/evening admin digest email via Resend.

Usage:
    python3 scripts/digest.py --morning
    python3 scripts/digest.py --evening
    python3 scripts/digest.py --morning --dry-run
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/andrew/src/health-engine")
DATA_DIR = PROJECT_ROOT / "data" / "users"
USERS_YAML = Path("/Users/andrew/.openclaw/workspace/users.yaml")
SESSIONS_DIR = Path("/Users/andrew/.openclaw/agents/main/sessions")
SESSIONS_JSON = SESSIONS_DIR / "sessions.json"
ENV_FILE = Path("/Users/andrew/.config/health-engine/.env")
TELEGRAM_LOG_DIR = Path("/tmp/openclaw")

FROM_EMAIL = "Baseline <andrew.deal@mybaseline.health>"
TO_EMAIL = "andrew.deal@mybaseline.health"

# All known users (order matters for display)
ALL_USERS = [
    "andrew", "paul", "grigoriy", "mike", "dad",
    "dean", "yusuf", "manny", "tommy", "patrick",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load RESEND_API_KEY from .env file."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _load_users_from_sqlite():
    """Load users from SQLite (canonical source)."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from engine.gateway.db import get_active_users, init_db
        init_db()
        return get_active_users()
    except Exception:
        return []


def load_users_yaml():
    """Load users → {phone: {user_id, name, ...}}. SQLite-first, yaml fallback."""
    users = _load_users_from_sqlite()
    if users:
        return {u["phone"]: u for u in users if u["phone"]}
    if not USERS_YAML.exists():
        return {}
    with open(USERS_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("users", {})


def phone_to_userid():
    """Return {phone: user_id} mapping."""
    users = _load_users_from_sqlite()
    if users:
        return {u["phone"]: u["user_id"] for u in users if u["phone"]}
    raw = load_users_yaml()
    return {phone: info.get("user_id", "") for phone, info in raw.items()}


def userid_to_phone():
    """Return {user_id: phone} mapping."""
    users = _load_users_from_sqlite()
    if users:
        return {u["user_id"]: u["phone"] for u in users if u["phone"]}
    raw = load_users_yaml()
    return {info.get("user_id", ""): phone for phone, info in raw.items()}


def userid_to_name():
    """Return {user_id: name} mapping."""
    users = _load_users_from_sqlite()
    if users:
        return {u["user_id"]: u["name"] for u in users}
    raw = load_users_yaml()
    mapping = {}
    for phone, info in raw.items():
        uid = info.get("user_id", "")
        name = info.get("name", uid.title())
        mapping[uid] = name
    return mapping


def read_csv_last_rows(path, n=5):
    """Read last n rows of a CSV, return list of dicts."""
    if not path.exists():
        return []
    rows = []
    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows[-n:]
    except Exception:
        return []


def read_json(path):
    """Read a JSON file, return dict or None."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def read_yaml_file(path):
    """Read a YAML file, return dict or None."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def file_mtime(path):
    """Get file modification time as datetime (UTC)."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def parse_date(s):
    """Parse a date string like '2026-03-25' into a date object."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def now_utc():
    return datetime.now(timezone.utc)


def safe_float(val, default=0.0):
    """Safely convert a value to float."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _has_wearable_sqlite(person_id: str) -> bool:
    """Check if person has any wearable data in wearable_daily."""
    try:
        from engine.gateway.db import get_db, init_db
        init_db()
        row = get_db().execute(
            "SELECT 1 FROM wearable_daily WHERE person_id = ? LIMIT 1",
            (person_id,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _resolve_person_id(user_id: str):
    """Resolve user_id to person_id from SQLite."""
    try:
        from engine.gateway.db import get_db, init_db
        init_db()
        row = get_db().execute(
            "SELECT id FROM person WHERE health_engine_user_id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data collection per user
# ---------------------------------------------------------------------------

def collect_user_data(user_id):
    """Collect summary data for a single user."""
    user_dir = DATA_DIR / user_id
    info = {"user_id": user_id, "files": [], "data_points": {}}

    if not user_dir.exists():
        info["status"] = "no_directory"
        return info

    # List what files exist
    files = sorted(f.name for f in user_dir.iterdir() if f.is_file())
    info["files"] = files

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    cutoff = yesterday

    # Weight log
    weight_rows = read_csv_last_rows(user_dir / "weight_log.csv", 3)
    if weight_rows:
        last = weight_rows[-1]
        w = safe_float(last.get("weight_lbs"))
        if w > 0:
            info["data_points"]["last_weight"] = f'{w:.1f} lbs'
        info["data_points"]["last_weight_date"] = last.get("date", "")
        recent_weight = [r for r in weight_rows if parse_date(r.get("date", "")) and parse_date(r.get("date", "")) >= cutoff]
        if recent_weight:
            info["data_points"]["weight_logged_recently"] = True

    # Meal log
    meal_rows = read_csv_last_rows(user_dir / "meal_log.csv", 20)
    if meal_rows:
        today_meals = [r for r in meal_rows if r.get("date") == str(today)]
        yesterday_meals = [r for r in meal_rows if r.get("date") == str(yesterday)]
        if today_meals:
            total_cal = sum(int(safe_float(r.get("calories"))) for r in today_meals)
            total_pro = sum(int(safe_float(r.get("protein_g"))) for r in today_meals)
            info["data_points"]["meals_today"] = f"{len(today_meals)} meals, {total_cal} cal, {total_pro}g protein"
        if yesterday_meals:
            total_cal = sum(int(safe_float(r.get("calories"))) for r in yesterday_meals)
            total_pro = sum(int(safe_float(r.get("protein_g"))) for r in yesterday_meals)
            info["data_points"]["meals_yesterday"] = f"{len(yesterday_meals)} meals, {total_cal} cal, {total_pro}g protein"
        info["data_points"]["last_meal_date"] = meal_rows[-1].get("date", "")

    # Daily habits
    habits_rows = read_csv_last_rows(user_dir / "daily_habits.csv", 5)
    if habits_rows:
        last_habit = habits_rows[-1]
        info["data_points"]["last_habit_date"] = last_habit.get("date", "")
        # Count y/n compliance
        yes_count = sum(1 for v in last_habit.values() if v == "y")
        no_count = sum(1 for v in last_habit.values() if v == "n")
        total = yes_count + no_count
        if total > 0:
            info["data_points"]["habit_compliance"] = f"{yes_count}/{total} ({100*yes_count//total}%)"

    # BP log
    bp_rows = read_csv_last_rows(user_dir / "bp_log.csv", 3)
    if bp_rows:
        last_bp = bp_rows[-1]
        info["data_points"]["last_bp"] = f'{last_bp.get("systolic")}/{last_bp.get("diastolic")}'
        info["data_points"]["last_bp_date"] = last_bp.get("date", "")

    # Strength log
    strength_rows = read_csv_last_rows(user_dir / "strength_log.csv", 3)
    if strength_rows:
        last_str = strength_rows[-1]
        info["data_points"]["last_strength"] = f'{last_str.get("exercise")} {last_str.get("weight_lbs")}x{last_str.get("reps")}'
        info["data_points"]["last_strength_date"] = last_str.get("date", "")

    # Briefing
    briefing = read_json(user_dir / "briefing.json")
    if briefing:
        info["briefing"] = briefing
        info["data_points"]["briefing_date"] = briefing.get("as_of", "")
        # Garmin metrics
        garmin = briefing.get("garmin", {})
        if garmin:
            info["data_points"]["hrv"] = garmin.get("hrv_rmssd_avg")
            info["data_points"]["rhr"] = garmin.get("resting_hr")
            info["data_points"]["sleep_avg"] = garmin.get("sleep_duration_avg")
            info["data_points"]["steps_avg"] = garmin.get("daily_steps_avg")
        # Coaching signals
        signals = briefing.get("coaching_signals", briefing.get("signals", []))
        if signals:
            info["data_points"]["coaching_signals"] = signals

    # Config
    config = read_yaml_file(user_dir / "config.yaml")
    if config:
        info["has_config"] = True
        profile = config.get("profile", {})
        if profile:
            info["data_points"]["profile_age"] = profile.get("age")
            info["data_points"]["profile_sex"] = profile.get("sex")

    # Context.md
    if (user_dir / "context.md").exists():
        info["has_context"] = True

    # Wearable data: SQLite first, JSON fallback
    pid = _resolve_person_id(user_id)
    if pid and _has_wearable_sqlite(pid):
        info["has_wearable"] = True
    else:
        if (user_dir / "apple_health_latest.json").exists():
            info["has_apple_health"] = True
        if (user_dir / "garmin_latest.json").exists():
            info["has_garmin"] = True

    # Lab results
    if (user_dir / "lab_results.json").exists():
        info["has_labs"] = True

    # Determine last activity date
    last_activity = determine_last_activity(info)
    info["last_activity"] = last_activity

    return info


def determine_last_activity(info):
    """Determine the most recent activity date from data points."""
    dates = []
    dp = info.get("data_points", {})
    for key in ["last_weight_date", "last_meal_date", "last_habit_date",
                "last_bp_date", "last_strength_date", "briefing_date"]:
        d = parse_date(dp.get(key, ""))
        if d:
            dates.append(d)

    # Also check file mtimes
    user_dir = DATA_DIR / info["user_id"]
    if user_dir.exists():
        for f in user_dir.iterdir():
            if f.is_file():
                mt = file_mtime(f)
                if mt:
                    dates.append(mt.date())

    return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Session transcript extraction
# ---------------------------------------------------------------------------

def load_sessions_index():
    """Load sessions.json → dict."""
    if not SESSIONS_JSON.exists():
        return {}
    try:
        with open(SESSIONS_JSON) as f:
            return json.load(f)
    except Exception:
        return {}


def find_session_for_phone(phone, sessions):
    """Find the most recent active session file for a phone number."""
    best = None
    best_updated = 0
    for key, meta in sessions.items():
        dc = meta.get("deliveryContext", {})
        session_phone = dc.get("to", meta.get("lastTo", ""))
        if session_phone == phone:
            sf = meta.get("sessionFile", "")
            updated = meta.get("updatedAt", 0)
            if sf and not sf.endswith(".deleted") and ".deleted." not in sf and ".reset." not in sf:
                if updated > best_updated:
                    best = sf
                    best_updated = updated
    return best, best_updated


def extract_recent_user_messages(session_file, max_messages=3):
    """Extract the last N user messages from a JSONL session file."""
    if not session_file or not Path(session_file).exists():
        return []

    messages = []
    try:
        with open(session_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "message":
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", entry.get("role", ""))
                if role != "user":
                    continue

                content = msg.get("content", entry.get("content", ""))
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    text = " ".join(text_parts)

                if not text:
                    continue

                # Skip cron/system messages
                if text.startswith("[cron:") or text.startswith("[system"):
                    continue

                # Strip WhatsApp metadata wrapper (Conversation info / Sender blocks)
                cleaned = text
                if "Conversation info (untrusted metadata):" in cleaned or "Sender (untrusted metadata):" in cleaned:
                    import re
                    # Remove code-fenced JSON blocks and their headers
                    cleaned = re.sub(
                        r'(?:Conversation info|Sender) \(untrusted metadata\):\s*```json\s*\{[^}]*\}\s*```',
                        '', cleaned, flags=re.DOTALL
                    )
                    # Also handle non-fenced metadata
                    cleaned = re.sub(r'Conversation info \(untrusted metadata\):.*?(?=\n\n|\Z)', '', cleaned, flags=re.DOTALL)
                    cleaned = re.sub(r'Sender \(untrusted metadata\):.*?(?=\n\n|\Z)', '', cleaned, flags=re.DOTALL)
                    cleaned = cleaned.strip()
                    if not cleaned:
                        cleaned = text[:100]  # Fallback

                # Strip media/system prefixes
                if cleaned.startswith("[media attached:"):
                    cleaned = cleaned.split("]", 1)[0] + "]"
                if cleaned.startswith("System:"):
                    continue

                text = cleaned.strip()
                if not text:
                    continue

                ts = entry.get("timestamp", msg.get("timestamp", ""))
                messages.append({"text": text[:300], "timestamp": str(ts)})

    except Exception:
        pass

    return messages[-max_messages:]


def get_session_info_for_users():
    """Get last messages for each user from session transcripts."""
    sessions = load_sessions_index()
    uid_to_phone = userid_to_phone()
    result = {}

    for user_id, phone in uid_to_phone.items():
        sf, updated = find_session_for_phone(phone, sessions)
        if sf:
            msgs = extract_recent_user_messages(sf, max_messages=3)
            channel = "unknown"
            # Determine channel from session key
            for key, meta in sessions.items():
                if meta.get("sessionFile") == sf:
                    channel = meta.get("deliveryContext", {}).get("channel", "unknown")
                    break
            result[user_id] = {
                "messages": msgs,
                "channel": channel,
                "last_updated": updated,
                "session_file": sf,
            }

    return result


# ---------------------------------------------------------------------------
# System health check
# ---------------------------------------------------------------------------

def check_api_health():
    """Quick health check on localhost:18800/health."""
    try:
        r = requests.get("http://localhost:18800/health", timeout=3)
        if r.status_code == 200:
            return "ok", r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        return "degraded", f"HTTP {r.status_code}"
    except requests.ConnectionError:
        return "down", "Connection refused"
    except Exception as e:
        return "error", str(e)[:100]


def check_telegram_logs():
    """Check for recent Telegram channel log activity."""
    if not TELEGRAM_LOG_DIR.exists():
        return "no log dir"
    logs = sorted(TELEGRAM_LOG_DIR.glob("openclaw-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return "no logs found"
    latest = logs[0]
    mt = file_mtime(latest)
    if mt:
        age = now_utc() - mt
        return f"last log {latest.name} — {age.total_seconds()/3600:.1f}h ago"
    return "unknown"


# ---------------------------------------------------------------------------
# Onboarding stage classification
# ---------------------------------------------------------------------------

def classify_stage(info, session_info):
    """Classify a user's onboarding stage."""
    uid = info["user_id"]
    files = info.get("files", [])
    has_config = info.get("has_config", False)
    has_context = info.get("has_context", False)
    dp = info.get("data_points", {})
    has_session = uid in session_info and len(session_info[uid].get("messages", [])) > 0

    # Data flowing: has CSV data with dates
    data_keys = ["last_weight_date", "last_meal_date", "last_habit_date", "last_bp_date", "last_strength_date"]
    has_data = any(dp.get(k) for k in data_keys)
    has_wearable = info.get("has_wearable", False) or info.get("has_garmin", False) or info.get("has_apple_health", False)
    has_habit = bool(dp.get("last_habit_date"))

    if has_habit:
        return "habit_set"
    if has_data or has_wearable:
        return "data_flowing"
    if has_session:
        return "first_message"
    if has_config or has_context:
        return "signed_up"
    if len(files) == 0:
        return "empty"
    return "signed_up"


STAGE_LABELS = {
    "habit_set": "Habit Set",
    "data_flowing": "Data Flowing",
    "first_message": "First Message",
    "signed_up": "Signed Up",
    "empty": "Not Engaged",
}


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def esc(s):
    """Minimal HTML escape."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_digest(period, all_info, session_info, api_status, telegram_status):
    """Render the full digest as an HTML string."""
    today = datetime.now()
    date_str = today.strftime("%b %-d %Y")
    period_label = "Morning" if period == "morning" else "Evening"
    cutoff_date = today.date() - timedelta(days=1)

    names = userid_to_name()

    # Classify users
    active_users = []
    stale_users = []
    not_engaged = []

    for info in all_info:
        uid = info["user_id"]
        files = info.get("files", [])
        last_act = info.get("last_activity")
        stage = classify_stage(info, session_info)

        if stage == "empty":
            not_engaged.append(info)
        elif last_act and last_act >= cutoff_date:
            active_users.append(info)
        elif last_act:
            stale_users.append(info)
        else:
            # Has files but no datable activity
            if stage == "signed_up":
                not_engaged.append(info)
            else:
                stale_users.append(info)

    html_parts = []

    # --- Styles ---
    html_parts.append(f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700&family=DM+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#09090b;color:#fafafa;font-family:DM Sans,-apple-system,sans-serif;font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased;">
<div style="max-width:680px;margin:0 auto;padding:32px 20px;">

<div style="padding-bottom:20px;margin-bottom:32px;border-bottom:1px solid #1c1c1f;">
  <div style="font-family:JetBrains Mono,monospace;font-size:0.65rem;color:#52525b;text-transform:uppercase;letter-spacing:0.14em;margin-bottom:8px;">BASELINE HEALTH ENGINE</div>
  <h1 style="margin:0;font-family:Barlow Condensed,sans-serif;font-size:2rem;font-weight:600;color:#fafafa;text-transform:uppercase;letter-spacing:0.02em;">{period_label} Digest</h1>
  <div style="margin-top:8px;font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#52525b;letter-spacing:0.1em;">{date_str} &middot; {today.strftime("%I:%M %p")} PT</div>
  <div style="display:inline-block;margin-top:12px;">
    <span style="display:inline-block;background:rgba(34,197,94,0.1);color:#22c55e;font-family:JetBrains Mono,monospace;font-size:0.6rem;font-weight:600;padding:4px 10px;border-radius:4px;letter-spacing:0.05em;margin-right:6px;">{len(active_users)} ACTIVE</span>
    <span style="display:inline-block;background:rgba(245,158,11,0.08);color:#f0b040;font-family:JetBrains Mono,monospace;font-size:0.6rem;font-weight:600;padding:4px 10px;border-radius:4px;letter-spacing:0.05em;margin-right:6px;">{len(stale_users)} STALE</span>
    <span style="display:inline-block;background:rgba(113,113,122,0.1);color:#71717a;font-family:JetBrains Mono,monospace;font-size:0.6rem;font-weight:600;padding:4px 10px;border-radius:4px;letter-spacing:0.05em;">{len(not_engaged)} WAITING</span>
  </div>
</div>
""")

    # --- Active Users ---
    if active_users:
        html_parts.append('<div style="display:flex;align-items:baseline;gap:12px;margin:32px 0 16px;padding-bottom:10px;border-bottom:1px solid #1c1c1f;"><span style="font-family:Barlow Condensed,sans-serif;font-size:1.3rem;font-weight:600;color:#fafafa;text-transform:uppercase;letter-spacing:0.04em;">Active</span><span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;color:#52525b;text-transform:uppercase;letter-spacing:0.1em;">LAST 24H</span></div>')
        for info in active_users:
            html_parts.append(render_user_card(info, session_info, names, active=True))

    # --- Stale Users ---
    if stale_users:
        html_parts.append('<div style="display:flex;align-items:baseline;gap:12px;margin:32px 0 16px;padding-bottom:10px;border-bottom:1px solid #1c1c1f;"><span style="font-family:Barlow Condensed,sans-serif;font-size:1.3rem;font-weight:600;color:#f0b040;text-transform:uppercase;letter-spacing:0.04em;">Stale</span><span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;color:#52525b;text-transform:uppercase;letter-spacing:0.1em;">24H+ SILENT</span></div>')
        for info in stale_users:
            html_parts.append(render_user_card(info, session_info, names, active=False))

    # --- Not Yet Engaged ---
    if not_engaged:
        html_parts.append('<div style="display:flex;align-items:baseline;gap:12px;margin:32px 0 16px;padding-bottom:10px;border-bottom:1px solid #1c1c1f;"><span style="font-family:Barlow Condensed,sans-serif;font-size:1.3rem;font-weight:600;color:#52525b;text-transform:uppercase;letter-spacing:0.04em;">Not Yet Engaged</span></div>')
        for info in not_engaged:
            uid = info["user_id"]
            name = names.get(uid, uid.title())
            files = info.get("files", [])
            file_str = ", ".join(files) if files else "empty directory"
            html_parts.append(f"""
<div style="background:#0e0e13;border:1px solid #1a1a24;border-radius:12px;padding:14px 24px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:baseline;">
  <div><span style="font-family:Barlow Condensed,sans-serif;font-size:1rem;font-weight:600;color:#52525b;text-transform:uppercase;">{esc(name)}</span>
  <span style="font-family:JetBrains Mono,monospace;font-size:0.5rem;color:#3f3f46;margin-left:8px;letter-spacing:0.1em;">{esc(uid).upper()}</span></div>
  <span style="font-family:JetBrains Mono,monospace;font-size:0.5rem;color:#3f3f46;letter-spacing:0.05em;">{esc(file_str)}</span>
</div>""")

    # --- System Health ---
    api_state, api_detail = api_status
    api_color = {"ok": "#4ae68a", "degraded": "#f0b040"}.get(api_state, "#f06060")
    html_parts.append(f"""
<div style="display:flex;align-items:baseline;gap:12px;margin:40px 0 16px;padding-bottom:10px;border-bottom:1px solid #1c1c1f;"><span style="font-family:Barlow Condensed,sans-serif;font-size:1.3rem;font-weight:600;color:#52525b;text-transform:uppercase;letter-spacing:0.04em;">System</span></div>
<div style="background:#0e0e13;border:1px solid #1a1a24;border-radius:12px;padding:16px 24px;margin-bottom:12px;">
  <table style="width:100%;border-collapse:collapse;font-family:DM Sans,-apple-system,sans-serif;font-size:0.85rem;">
    <tr><td style="padding:6px 0;color:#68687a;font-family:JetBrains Mono,monospace;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;">API</td><td style="padding:6px 0;text-align:right;"><span style="color:{api_color};font-family:JetBrains Mono,monospace;font-size:0.7rem;font-weight:600;">{esc(api_state).upper()}</span></td></tr>
    <tr><td style="padding:6px 0;color:#68687a;font-family:JetBrains Mono,monospace;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;">TELEGRAM</td><td style="padding:6px 0;text-align:right;color:#a1a1aa;font-size:0.8rem;">{esc(telegram_status)}</td></tr>
  </table>
</div>
""")

    # --- Onboarding Pipeline ---
    html_parts.append('<div style="display:flex;align-items:baseline;gap:12px;margin:40px 0 16px;padding-bottom:10px;border-bottom:1px solid #1c1c1f;"><span style="font-family:\'Barlow Condensed\',sans-serif;font-size:1.3rem;font-weight:600;color:#52525b;text-transform:uppercase;letter-spacing:0.04em;">Pipeline</span></div>')
    html_parts.append('<div style="background:#0e0e13;border:1px solid #1a1a24;border-radius:12px;padding:20px 24px;margin-bottom:12px;">')

    stages = {}
    for info in all_info:
        stage = classify_stage(info, session_info)
        stages.setdefault(stage, []).append(info["user_id"])

    stage_order = ["habit_set", "data_flowing", "first_message", "signed_up", "empty"]
    stage_colors = {
        "habit_set": "#4ae68a",
        "data_flowing": "#60a0f0",
        "first_message": "#f0b040",
        "signed_up": "#a1a1aa",
        "empty": "#52525b",
    }
    for stage in stage_order:
        uids = stages.get(stage, [])
        if not uids:
            continue
        color = stage_colors.get(stage, "#a1a1aa")
        label = STAGE_LABELS.get(stage, stage)
        user_names = [names.get(u, u.title()) for u in uids]
        html_parts.append(f"""
  <div style="margin-bottom:10px;display:flex;align-items:center;">
    <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:10px;flex-shrink:0;"></span>
    <span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:{color};font-weight:600;text-transform:uppercase;letter-spacing:0.1em;min-width:120px;">{esc(label)}</span>
    <span style="color:#a1a1aa;font-size:0.8rem;margin-left:8px;">{esc(', '.join(user_names))}</span>
  </div>""")

    html_parts.append('</div>')

    # --- Footer ---
    html_parts.append(f"""
<div style="margin-top:40px;padding-top:16px;border-top:1px solid #1c1c1f;text-align:center;">
  <span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;color:#3f3f46;text-transform:uppercase;letter-spacing:0.14em;">BASELINE HEALTH ENGINE &middot; {today.strftime("%Y-%m-%d %H:%M")} PT</span>
</div>

</div>
</body>
</html>""")

    return "\n".join(html_parts)


def render_user_card(info, session_info, names, active=True):
    """Render a single user card."""
    uid = info["user_id"]
    name = names.get(uid, uid.title())
    dp = info.get("data_points", {})
    last_act = info.get("last_activity")
    si = session_info.get(uid, {})
    channel = si.get("channel", "—")
    messages = si.get("messages", [])

    border_color = "#22c55e" if active else "#f59e0b"

    parts = []
    parts.append(f"""
<div style="background:#0e0e13;border:1px solid #1a1a24;border-radius:12px;padding:20px 24px;margin-bottom:12px;">
  <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:baseline;">
    <div>
      <span style="font-family:Barlow Condensed,sans-serif;font-size:1.1rem;font-weight:600;color:#fafafa;text-transform:uppercase;letter-spacing:0.04em;">{esc(name)}</span>
      <span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;color:#52525b;margin-left:8px;letter-spacing:0.1em;">{esc(uid).upper()}</span>
    </div>
    <span style="font-family:JetBrains Mono,monospace;font-size:0.55rem;color:#52525b;letter-spacing:0.05em;">{esc(channel).upper()}</span>
  </div>""")

    # Data points
    metrics = []
    if dp.get("last_weight"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">WEIGHT</span> {esc(dp["last_weight"])}')
    if dp.get("meals_today"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">TODAY</span> {esc(dp["meals_today"])}')
    elif dp.get("meals_yesterday"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">YESTERDAY</span> {esc(dp["meals_yesterday"])}')
    if dp.get("habit_compliance"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">HABITS</span> {esc(dp["habit_compliance"])}')
    if dp.get("last_bp"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">BP</span> {esc(dp["last_bp"])}')
    if dp.get("hrv"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">HRV</span> {esc(dp["hrv"])}')
    if dp.get("rhr"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">RHR</span> {esc(dp["rhr"])}')
    if dp.get("sleep_avg"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">SLEEP</span> {esc(dp["sleep_avg"])}h')
    if dp.get("steps_avg"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">STEPS</span> {esc(dp["steps_avg"])}')
    if dp.get("last_strength"):
        metrics.append(f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#68687a;text-transform:uppercase;letter-spacing:0.08em;">LIFT</span> {esc(dp["last_strength"])}')

    if metrics:
        parts.append(f'  <div style="font-family:\'DM Sans\',-apple-system,sans-serif;font-size:0.8rem;color:#a1a1aa;margin-bottom:10px;line-height:1.8;">{" &nbsp;&middot;&nbsp; ".join(metrics)}</div>')

    # Coaching signals
    signals = dp.get("coaching_signals", [])
    if signals:
        if isinstance(signals, list):
            for sig in signals[:3]:
                if isinstance(sig, dict):
                    sig_text = sig.get("title", sig.get("message", sig.get("text", "")))
                    sig_body = sig.get("body", "")
                    if sig_text and sig_body:
                        sig_text = f"{sig_text} — {sig_body}"
                    elif not sig_text:
                        sig_text = str(sig)
                else:
                    sig_text = str(sig)
                parts.append(f'  <div style="font-size:12px;color:#f0b040;margin-bottom:2px;font-family:\'DM Sans\',-apple-system,sans-serif;">&#9888; {esc(sig_text[:160])}</div>')

    # Last activity
    if last_act:
        parts.append(f'  <div style="font-size:12px;color:#52525b;margin-bottom:6px;">Last activity: {esc(str(last_act))}</div>')

    # Recent messages
    if messages:
        parts.append('  <div style="margin-top:12px;padding-top:12px;border-top:1px solid #1a1a24;">')
        parts.append('    <div style="font-family:\'JetBrains Mono\',monospace;font-size:0.5rem;color:#3f3f46;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.14em;">RECENT</div>')
        for m in messages[-3:]:
            text = m.get("text", "")
            if len(text) > 180:
                text = text[:180] + "..."
            ts = m.get("timestamp", "")
            ts_label = ""
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_label = dt.strftime("%b %-d %H:%M")
                except Exception:
                    ts_label = str(ts)[:16]
            parts.append(f'    <div style="font-size:0.8rem;color:#b8b8c8;margin-bottom:4px;padding:6px 10px;background:#050507;border-radius:6px;border:1px solid #1a1a24;"><span style="font-family:\'JetBrains Mono\',monospace;font-size:0.5rem;color:#3f3f46;margin-right:8px;">{esc(ts_label)}</span>{esc(text)}</div>')
        parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

def send_email(html, subject, api_key, dry_run=False):
    """Send HTML email via Resend API."""
    if dry_run:
        print(html)
        return True

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [TO_EMAIL],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )

    if resp.status_code in (200, 201):
        data = resp.json()
        print(f"Email sent. ID: {data.get('id', 'n/a')}")
        return True
    else:
        print(f"Failed to send email: {resp.status_code} {resp.text}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate and send Baseline digest email")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--morning", action="store_true", help="Morning digest")
    group.add_argument("--evening", action="store_true", help="Evening digest")
    parser.add_argument("--dry-run", action="store_true", help="Print HTML to stdout instead of sending")
    args = parser.parse_args()

    period = "morning" if args.morning else "evening"

    # Load env
    env = load_env()
    api_key = env.get("RESEND_API_KEY", "")
    if not api_key and not args.dry_run:
        print("Error: RESEND_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    # Collect data for all users
    all_info = []
    for uid in ALL_USERS:
        info = collect_user_data(uid)
        all_info.append(info)

    # Session info
    session_info = get_session_info_for_users()

    # System health
    api_status = check_api_health()
    telegram_status = check_telegram_logs()

    # Render
    html = render_digest(period, all_info, session_info, api_status, telegram_status)

    # Subject
    today = datetime.now()
    date_str = today.strftime("%b %-d")
    subject = f"Baseline Digest — {'Morning' if period == 'morning' else 'Evening'}, {date_str}"

    # Send
    send_email(html, subject, api_key, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
