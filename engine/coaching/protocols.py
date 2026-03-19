"""Protocol-aware coaching — load protocols, compute progress, surface nudges."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml


PROTOCOLS_DIR = Path(__file__).parent.parent.parent / "protocols"


def load_protocol(name: str, protocols_dir: Optional[Path] = None) -> Optional[dict]:
    """Load a protocol YAML by name. Returns None if not found."""
    d = protocols_dir or PROTOCOLS_DIR
    path = d / f"{name}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def protocol_progress(
    protocol: dict,
    started: str,
    habit_data: Optional[list],
    garmin: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> dict:
    """
    Compute progress for an active protocol.

    Args:
        protocol: Parsed protocol YAML dict
        started: ISO date string when protocol was started
        habit_data: Rows from daily_habits.csv (wide format)
        garmin: Garmin latest metrics dict
        as_of: Reference date (defaults to today)

    Returns:
        Dict with day, week, phase, last_night habits, phase avg, top nudge, outcomes
    """
    ref = datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else datetime.now().date()
    if isinstance(started, str):
        start_date = datetime.strptime(started, "%Y-%m-%d").date()
    else:
        start_date = started  # YAML may parse dates as datetime.date

    day_number = (ref - start_date).days + 1
    week_number = ((ref - start_date).days // 7) + 1

    # Determine current phase
    phases = protocol.get("phases", [])
    current_phase = None
    for phase in phases:
        weeks = phase.get("weeks", [])
        if week_number in weeks:
            current_phase = phase
            break
    # If past all phases, use last phase
    if current_phase is None and phases:
        current_phase = phases[-1]

    # Get protocol habit IDs
    protocol_habits = protocol.get("habits", [])
    habit_ids = [h["id"] for h in protocol_habits]

    # Compute last night's habit completion
    last_night = _last_night_completion(habit_data, habit_ids, ref)

    # Compute phase average completion
    phase_avg = _phase_avg_completion(habit_data, habit_ids, start_date, current_phase, ref)

    # Top nudge: highest-priority missed habit from last night
    top_nudge = _top_nudge(protocol_habits, last_night.get("missed", []))

    # Outcome metrics
    outcomes = _outcome_status(protocol.get("outcome_metrics", []), garmin)

    result = {
        "name": protocol.get("name", "Unknown"),
        "day": day_number,
        "week": week_number,
        "phase": current_phase.get("name", "") if current_phase else "",
        "phase_focus": current_phase.get("focus", "") if current_phase else "",
        "last_night": last_night,
        "phase_avg_completion": phase_avg,
        "top_nudge": top_nudge,
        "outcomes": outcomes,
    }

    return result


def _last_night_completion(
    habit_data: Optional[list],
    habit_ids: list[str],
    ref_date,
) -> dict:
    """Check which protocol habits were completed on the reference date (or day before)."""
    if not habit_data:
        return {"hit": 0, "total": len(habit_ids), "missed": habit_ids[:]}

    # Look for yesterday's data (last night's habits) or today's
    yesterday = (ref_date - timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = ref_date.strftime("%Y-%m-%d")

    # Try yesterday first (last night's sleep habits), then today
    row = None
    for r in habit_data:
        if r.get("date") == yesterday:
            row = r
            break
    if row is None:
        for r in habit_data:
            if r.get("date") == today_str:
                row = r
                break

    if row is None:
        return {"hit": 0, "total": len(habit_ids), "missed": habit_ids[:]}

    hit = 0
    missed = []
    tracked = 0
    for hid in habit_ids:
        val = row.get(hid, "").strip().lower()
        if val == "":
            # Not tracked — don't count against
            continue
        tracked += 1
        if val in ("y", "yes", "true", "1"):
            hit += 1
        else:
            missed.append(hid)

    return {
        "hit": hit,
        "total": tracked if tracked > 0 else len(habit_ids),
        "missed": missed,
    }


def _phase_avg_completion(
    habit_data: Optional[list],
    habit_ids: list[str],
    start_date,
    current_phase: Optional[dict],
    ref_date,
) -> Optional[float]:
    """Average habit completion rate across days in the current phase."""
    if not habit_data or not current_phase:
        return None

    weeks = current_phase.get("weeks", [])
    if not weeks:
        return None

    phase_start = start_date + timedelta(weeks=min(weeks) - 1)
    phase_end = min(ref_date, start_date + timedelta(weeks=max(weeks)) - timedelta(days=1))

    if phase_start > ref_date:
        return None

    daily_rates = []
    for row in habit_data:
        row_date_str = row.get("date", "")
        try:
            row_date = datetime.strptime(row_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if row_date < phase_start or row_date > phase_end:
            continue

        tracked = 0
        hit = 0
        for hid in habit_ids:
            val = row.get(hid, "").strip().lower()
            if val == "":
                continue
            tracked += 1
            if val in ("y", "yes", "true", "1"):
                hit += 1
        if tracked > 0:
            daily_rates.append(hit / tracked)

    if not daily_rates:
        return None
    return round(sum(daily_rates) / len(daily_rates) * 100, 1)


def _top_nudge(protocol_habits: list[dict], missed: list[str]) -> Optional[dict]:
    """Return the highest-priority missed habit's nudge."""
    if not missed:
        return None

    missed_set = set(missed)
    # Sort by priority (lower number = higher priority)
    candidates = [h for h in protocol_habits if h["id"] in missed_set]
    candidates.sort(key=lambda h: h.get("priority", 999))

    if not candidates:
        return None

    top = candidates[0]
    return {
        "habit": top["id"],
        "label": top.get("label", top["id"]),
        "nudge": top.get("nudge", ""),
    }


def _outcome_status(outcome_metrics: list[dict], garmin: Optional[dict]) -> list[dict]:
    """Check current values of outcome metrics against targets."""
    if not outcome_metrics:
        return []

    results = []
    for metric in outcome_metrics:
        mid = metric["id"]
        source = metric.get("source", "")
        target = metric.get("target")
        direction = metric.get("direction", "higher_is_better")

        current = None
        if source == "garmin" and garmin:
            current = garmin.get(mid)

        status = None
        if current is not None and target is not None:
            if direction == "lower_is_better":
                status = "on_track" if current <= target else "above_target"
            else:
                status = "on_track" if current >= target else "below_target"

        results.append({
            "id": mid,
            "current": current,
            "target": target,
            "unit": metric.get("unit", ""),
            "status": status,
        })

    return results
