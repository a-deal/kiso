#!/usr/bin/env python3
"""health-engine CLI — score profiles, generate insights, pull Garmin data."""

import argparse
import json
import sys
from pathlib import Path

import yaml

from engine.models import Demographics, UserProfile
from engine.scoring.engine import score_profile, print_report
from engine.insights.engine import generate_insights, load_rules


def load_config(path: str) -> dict:
    """Load config.yaml."""
    p = Path(path)
    if not p.exists():
        print(f"Config not found: {p}")
        print("Copy config.example.yaml → config.yaml and fill in your values.")
        sys.exit(1)
    with open(p) as f:
        return yaml.safe_load(f)


def cmd_score(args):
    """Score a user profile."""
    config = load_config(args.config)
    profile_cfg = config.get("profile", {})

    # Build profile from config + any Garmin data on disk
    demo = Demographics(
        age=profile_cfg.get("age", 35),
        sex=profile_cfg.get("sex", "M"),
    )
    profile = UserProfile(demographics=demo)

    # Load Garmin data if available
    data_dir = Path(config.get("data_dir", "./data"))
    garmin_path = data_dir / "garmin_latest.json"
    if garmin_path.exists():
        with open(garmin_path) as f:
            garmin = json.load(f)
        profile.resting_hr = garmin.get("resting_hr")
        profile.daily_steps_avg = garmin.get("daily_steps_avg")
        profile.sleep_regularity_stddev = garmin.get("sleep_regularity_stddev")
        profile.sleep_duration_avg = garmin.get("sleep_duration_avg")
        profile.vo2_max = garmin.get("vo2_max")
        profile.hrv_rmssd_avg = garmin.get("hrv_rmssd_avg")
        profile.zone2_min_per_week = garmin.get("zone2_min_per_week")

    # Load from profile JSON if provided
    if args.profile:
        with open(args.profile) as f:
            data = json.load(f)
        demo_data = data.pop("demographics", {})
        profile = UserProfile(
            demographics=Demographics(**demo_data),
            **{k: v for k, v in data.items() if hasattr(UserProfile, k)},
        )

    output = score_profile(profile)
    print_report(output)

    # Also output JSON if requested
    if args.json:
        json_output = {
            k: v for k, v in output.items()
            if k not in ("results", "gaps")
        }
        json_output["results"] = [r.to_dict() for r in output["results"]]
        json_output["gaps"] = [r.to_dict() for r in output["gaps"]]
        print(json.dumps(json_output, indent=2))


def cmd_insights(args):
    """Generate health insights."""
    config = load_config(args.config)
    data_dir = Path(config.get("data_dir", "./data"))

    # Load Garmin data
    garmin = None
    garmin_path = data_dir / "garmin_latest.json"
    if garmin_path.exists():
        with open(garmin_path) as f:
            garmin = json.load(f)

    # Load daily series for trends
    trends = None
    series_path = data_dir / "garmin_daily.json"
    if series_path.exists():
        with open(series_path) as f:
            series = json.load(f)
        rhr_pts = [{"rhr": e["rhr"]} for e in series if e.get("rhr") is not None]
        hrv_pts = [{"hrv": e["hrv"]} for e in series if e.get("hrv") is not None]
        if rhr_pts or hrv_pts:
            trends = {"rhr_pts": rhr_pts, "hrv_pts": hrv_pts}

    # Load weight data
    weights = None
    weight_path = data_dir / "weight_log.csv"
    if weight_path.exists():
        from engine.utils.csv_io import read_csv
        rows = read_csv(weight_path)
        weights = [{"weight": float(r["weight_lbs"]), "date": r["date"]}
                    for r in rows if r.get("weight_lbs")]

    # Load BP data
    bp_readings = None
    bp_path = data_dir / "bp_log.csv"
    if bp_path.exists():
        from engine.utils.csv_io import read_csv
        rows = read_csv(bp_path)
        bp_readings = [{"sys": float(r["systolic"]), "dia": float(r["diastolic"])}
                       for r in rows if r.get("systolic")]

    # Load rules
    rules_file = config.get("insights", {}).get("thresholds_file")
    rules = load_rules(rules_file) if rules_file else load_rules()

    insights = generate_insights(
        garmin=garmin,
        weights=weights,
        bp_readings=bp_readings,
        trends=trends,
        rules=rules,
    )

    if not insights:
        print("No insights generated — add more data.")
        return

    for ins in insights:
        severity_icons = {
            "critical": "!!",
            "warning": " !",
            "positive": " +",
            "neutral": " ~",
        }
        icon = severity_icons.get(ins.severity, "  ")
        print(f"  [{icon}] {ins.title}")
        print(f"      {ins.body}")
        print()


def cmd_pull(args):
    """Pull data from Garmin Connect."""
    config = load_config(args.config)

    from engine.integrations.garmin import GarminClient
    client = GarminClient.from_config(config)
    client.pull_all(
        history=args.history,
        history_days=args.history_days,
        workouts=args.workouts,
        workout_days=args.workout_days,
    )


def cmd_briefing(args):
    """Generate a full coaching briefing (JSON snapshot of all health data)."""
    config = load_config(args.config)

    from engine.coaching.briefing import build_briefing
    briefing = build_briefing(config)

    print(json.dumps(briefing, indent=2))


def cmd_status(args):
    """Show current data status."""
    config = load_config(args.config)
    data_dir = Path(config.get("data_dir", "./data"))

    print(f"\n  Data directory: {data_dir.resolve()}")
    print()

    files = [
        ("garmin_latest.json", "Garmin metrics"),
        ("garmin_daily.json", "Daily series (trends)"),
        ("garmin_daily_burn.json", "Daily calorie burn"),
        ("garmin_workouts.json", "Workout history"),
        ("weight_log.csv", "Weight log"),
        ("meal_log.csv", "Meal log"),
        ("strength_log.csv", "Strength log"),
        ("bp_log.csv", "Blood pressure log"),
    ]

    for filename, label in files:
        p = data_dir / filename
        if p.exists():
            size = p.stat().st_size
            mod = p.stat().st_mtime
            from datetime import datetime
            mod_str = datetime.fromtimestamp(mod).strftime("%Y-%m-%d %H:%M")
            print(f"  ✓ {label:<25} {filename:<30} {size:>8,} bytes  ({mod_str})")
        else:
            print(f"  ✗ {label:<25} {filename:<30} missing")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="health-engine",
        description="Health intelligence engine — scoring, insights, wearable integrations",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    sub = parser.add_subparsers(dest="command")

    # score
    p_score = sub.add_parser("score", help="Score a health profile")
    p_score.add_argument("--profile", help="Path to profile JSON file")
    p_score.add_argument("--json", action="store_true", help="Also output JSON")
    p_score.set_defaults(func=cmd_score)

    # insights
    p_insights = sub.add_parser("insights", help="Generate health insights")
    p_insights.set_defaults(func=cmd_insights)

    # briefing
    p_briefing = sub.add_parser("briefing", help="Full coaching briefing (JSON)")
    p_briefing.set_defaults(func=cmd_briefing)

    # pull
    p_pull = sub.add_parser("pull", help="Pull data from integrations")
    p_pull.add_argument("source", choices=["garmin"], help="Data source")
    p_pull.add_argument("--history", action="store_true", help="Pull daily series")
    p_pull.add_argument("--history-days", type=int, default=90)
    p_pull.add_argument("--workouts", action="store_true", help="Pull workouts")
    p_pull.add_argument("--workout-days", type=int, default=7)
    p_pull.set_defaults(func=cmd_pull)

    # status
    p_status = sub.add_parser("status", help="Show data status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
