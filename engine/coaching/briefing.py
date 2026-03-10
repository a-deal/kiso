"""Briefing assembly — gathers all available data into a single coaching snapshot.

This is the data layer for AI coaching. One call produces everything Claude
(or any LLM) needs to assess where the user stands and coach them forward.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from engine.models import Demographics, UserProfile
from engine.scoring.engine import score_profile
from engine.insights.engine import generate_insights, load_rules
from engine.insights.coaching import assess_sleep_debt, assess_deficit_impact, assess_taper_readiness
from engine.tracking.weight import rolling_average, weekly_rate, projected_date, rate_assessment
from engine.tracking.nutrition import remaining_to_hit, daily_totals, protein_check
from engine.tracking.strength import est_1rm, progression_summary
from engine.tracking.habits import streak, gap_analysis
from engine.utils.csv_io import read_csv


def build_briefing(config: dict) -> dict:
    """
    Assemble a complete health briefing from all available data.

    Reads config, data files, and runs scoring + insights to produce
    a single structured snapshot suitable for LLM coaching.

    Args:
        config: Parsed config.yaml dict

    Returns:
        Dict with sections: meta, score, insights, weight, nutrition,
        strength, habits, garmin, coaching, gaps
    """
    data_dir = Path(config.get("data_dir", "./data"))
    profile_cfg = config.get("profile", {})
    targets = config.get("targets", {})
    today = datetime.now().strftime("%Y-%m-%d")

    briefing = {
        "as_of": today,
        "data_available": {},
    }

    # --- Garmin data ---
    garmin = _load_json(data_dir / "garmin_latest.json")
    garmin_daily = _load_json(data_dir / "garmin_daily.json")
    briefing["data_available"]["garmin"] = garmin is not None
    briefing["data_available"]["garmin_daily"] = garmin_daily is not None

    if garmin:
        briefing["garmin"] = {
            "last_updated": garmin.get("last_updated"),
            "hrv_rmssd_avg": garmin.get("hrv_rmssd_avg"),
            "resting_hr": garmin.get("resting_hr"),
            "sleep_duration_avg": garmin.get("sleep_duration_avg"),
            "sleep_regularity_stddev": garmin.get("sleep_regularity_stddev"),
            "vo2_max": garmin.get("vo2_max"),
            "daily_steps_avg": garmin.get("daily_steps_avg"),
            "zone2_min_per_week": garmin.get("zone2_min_per_week"),
        }

    # --- Score ---
    demo = Demographics(
        age=profile_cfg.get("age", 35),
        sex=profile_cfg.get("sex", "M"),
    )
    profile = UserProfile(demographics=demo)

    if garmin:
        profile.resting_hr = garmin.get("resting_hr")
        profile.daily_steps_avg = garmin.get("daily_steps_avg")
        profile.sleep_regularity_stddev = garmin.get("sleep_regularity_stddev")
        profile.sleep_duration_avg = garmin.get("sleep_duration_avg")
        profile.vo2_max = garmin.get("vo2_max")
        profile.hrv_rmssd_avg = garmin.get("hrv_rmssd_avg")
        profile.zone2_min_per_week = garmin.get("zone2_min_per_week")

    score_output = score_profile(profile)
    briefing["score"] = {
        "coverage": score_output["coverage_score"],
        "avg_percentile": score_output["avg_percentile"],
        "tier1_pct": score_output["tier1_pct"],
        "tier2_pct": score_output["tier2_pct"],
        "results": [r.to_dict() for r in score_output["results"] if r.has_data],
        "gap_count": len(score_output["gaps"]),
        "top_gaps": [
            {"name": g.name, "weight": g.coverage_weight, "cost": g.cost_to_close}
            for g in score_output["gaps"][:5]
        ],
    }

    # --- Insights ---
    weights_data = _load_weight_log(data_dir)
    bp_data = _load_bp_log(data_dir)
    trends = _build_trends(garmin_daily)
    briefing["data_available"]["weight_log"] = weights_data is not None
    briefing["data_available"]["bp_log"] = bp_data is not None

    rules_file = config.get("insights", {}).get("thresholds_file")
    rules = load_rules(rules_file) if rules_file else load_rules()

    insights = generate_insights(
        garmin=garmin,
        weights=weights_data,
        bp_readings=bp_data,
        trends=trends,
        rules=rules,
    )
    briefing["insights"] = [
        {"severity": i.severity, "category": i.category, "title": i.title, "body": i.body}
        for i in insights
    ]

    # --- Weight ---
    if weights_data and len(weights_data) >= 2:
        rolled = rolling_average(weights_data)
        rate = weekly_rate(weights_data)
        current = weights_data[-1]["weight"]
        target_w = targets.get("weight_lbs")

        weight_section = {
            "current": current,
            "rolling_avg_7d": rolled[-1]["rolling_avg"] if rolled else None,
            "weekly_rate": rate,
            "entries": len(weights_data),
        }

        if rate and current:
            weight_section["rate_assessment"] = rate_assessment(rate, current)

        if target_w and rate and rate > 0:
            weight_section["target"] = target_w
            weight_section["remaining"] = round(current - target_w, 1)
            weight_section["projected_date"] = projected_date(current, target_w, rate)

        briefing["weight"] = weight_section

    # --- Nutrition (today) ---
    meals_today = _load_meals_for_date(data_dir, today)
    briefing["data_available"]["meal_log"] = (data_dir / "meal_log.csv").exists()

    if meals_today:
        totals = daily_totals(meals_today)
        briefing["nutrition"] = {"today_totals": totals}

        if targets.get("protein_g") or targets.get("calories_training"):
            macro_targets = {
                "protein": targets.get("protein_g", 0),
                "calories": targets.get("calories_training", 0),
            }
            remaining = remaining_to_hit(meals_today, macro_targets)
            briefing["nutrition"]["remaining"] = remaining

            if targets.get("protein_g"):
                warn = protein_check(totals["protein_g"], targets["protein_g"])
                if warn:
                    briefing["nutrition"]["protein_warning"] = warn

    # --- Strength ---
    strength_data = _load_strength_log(data_dir, config)
    briefing["data_available"]["strength_log"] = strength_data is not None

    if strength_data:
        exercises = set(s.get("exercise") for s in strength_data if s.get("exercise"))
        strength_section = {}
        for ex in sorted(exercises):
            prog = progression_summary(strength_data, ex)
            if prog:
                strength_section[ex] = {
                    "current_1rm": prog["current_1rm"],
                    "peak_1rm": prog["peak_1rm"],
                    "peak_pct": prog["peak_pct"],
                    "total_sets": prog["total_sets"],
                }
        if strength_section:
            briefing["strength"] = strength_section

    # --- Habits ---
    habit_data = _load_habits(data_dir)
    briefing["data_available"]["daily_habits"] = habit_data is not None

    if habit_data:
        habits_section = {}
        habit_names = set(h["habit"] for h in habit_data)
        for habit_name in sorted(habit_names):
            completed_dates = [
                h["date"] for h in habit_data
                if h["habit"] == habit_name and h.get("completed", "").lower() in ("yes", "true", "1")
            ]
            ga = gap_analysis(completed_dates, window_days=30, as_of=today)
            habits_section[habit_name] = {
                "current_streak": ga["current_streak"],
                "completion_rate": ga["completion_rate"],
                "longest_streak": ga["longest_streak"],
            }
        if habits_section:
            briefing["habits"] = habits_section

    # --- Coaching signals (compound) ---
    coaching_signals = []

    if garmin:
        sleep_debt = assess_sleep_debt(garmin.get("sleep_duration_avg"))
        if sleep_debt:
            coaching_signals.append({
                "severity": sleep_debt.severity,
                "title": sleep_debt.title,
                "body": sleep_debt.body,
            })

    rate = briefing.get("weight", {}).get("weekly_rate")
    if rate is not None:
        deficit = assess_deficit_impact(
            rate,
            garmin.get("hrv_rmssd_avg") if garmin else None,
            garmin.get("resting_hr") if garmin else None,
        )
        if deficit:
            coaching_signals.append({
                "severity": deficit.severity,
                "title": deficit.title,
                "body": deficit.body,
            })

        target_w = targets.get("weight_lbs")
        current_w = briefing.get("weight", {}).get("current")
        if target_w and current_w:
            taper = assess_taper_readiness(
                weeks_in_deficit=None,  # TODO: track deficit start date in config
                weight_current=current_w,
                weight_target=target_w,
                weekly_loss_rate=rate,
            )
            if taper:
                coaching_signals.append({
                    "severity": taper.severity,
                    "title": taper.title,
                    "body": taper.body,
                })

    if coaching_signals:
        briefing["coaching_signals"] = coaching_signals

    return briefing


# --- Data loading helpers ---

def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _load_weight_log(data_dir: Path) -> Optional[list]:
    path = data_dir / "weight_log.csv"
    if not path.exists():
        return None
    rows = read_csv(path)
    weights = [
        {"weight": float(r["weight_lbs"]), "date": r["date"]}
        for r in rows if r.get("weight_lbs")
    ]
    return weights if weights else None


def _load_bp_log(data_dir: Path) -> Optional[list]:
    path = data_dir / "bp_log.csv"
    if not path.exists():
        return None
    rows = read_csv(path)
    readings = [
        {"sys": float(r["systolic"]), "dia": float(r["diastolic"])}
        for r in rows if r.get("systolic")
    ]
    return readings if readings else None


def _load_meals_for_date(data_dir: Path, date: str) -> Optional[list]:
    path = data_dir / "meal_log.csv"
    if not path.exists():
        return None
    rows = read_csv(path)
    meals = [r for r in rows if r.get("date") == date]
    return meals if meals else None


def _load_strength_log(data_dir: Path, config: dict) -> Optional[list]:
    path = data_dir / "strength_log.csv"
    if not path.exists():
        return None
    rows = read_csv(path)
    exercise_map = config.get("exercise_name_map", {})
    for r in rows:
        raw_name = (r.get("exercise") or "").lower().strip()
        r["exercise"] = exercise_map.get(raw_name, raw_name)
    return rows if rows else None


def _load_habits(data_dir: Path) -> Optional[list]:
    path = data_dir / "daily_habits.csv"
    if not path.exists():
        return None
    rows = read_csv(path)
    return rows if rows else None


def _build_trends(garmin_daily) -> Optional[dict]:
    if not garmin_daily or not isinstance(garmin_daily, list):
        return None
    rhr_pts = [{"rhr": e["rhr"]} for e in garmin_daily if e.get("rhr") is not None]
    hrv_pts = [{"hrv": e["hrv"]} for e in garmin_daily if e.get("hrv") is not None]
    if rhr_pts or hrv_pts:
        return {"rhr_pts": rhr_pts, "hrv_pts": hrv_pts}
    return None
