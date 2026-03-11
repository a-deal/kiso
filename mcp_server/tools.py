"""Tool definitions for the Health Engine MCP server."""

import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from engine.utils.csv_io import read_csv, write_csv

# User home directory for pip-installed (uvx) usage
_USER_HOME = Path(os.path.expanduser("~/.config/health-engine"))


def _config_path() -> Path:
    """Find config.yaml — check PROJECT_ROOT first, then user home."""
    local = PROJECT_ROOT / "config.yaml"
    if local.exists():
        return local
    home = _USER_HOME / "config.yaml"
    return home


def _load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _data_dir() -> Path:
    config = _load_config()
    rel = config.get("data_dir", None)
    if rel:
        # If config exists at PROJECT_ROOT, resolve relative to it
        config_dir = _config_path().parent
        return (config_dir / rel).resolve()
    # Default: ~/.config/health-engine/data for uvx, ./data for local dev
    if (PROJECT_ROOT / "config.yaml").exists():
        return (PROJECT_ROOT / "data").resolve()
    data = _USER_HOME / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data


def _load_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def register_tools(mcp: FastMCP):
    """Register all Health Engine tools on the given MCP server."""

    @mcp.tool()
    def checkin(greeting: str = "morning check-in") -> dict:
        """Full health coaching briefing — scores, insights, weight, nutrition, habits, protocols, Garmin data. Call this first when the user asks about their health. Pass a short greeting like 'morning check-in' or 'how am I doing'."""
        from engine.coaching.briefing import build_briefing

        config = _load_config()
        return build_briefing(config)

    @mcp.tool()
    def score() -> dict:
        """Scoring engine deep-dive: coverage %, NHANES percentiles for 20 metrics, tier breakdown, and ranked gap analysis showing what to measure next."""
        from engine.models import Demographics, UserProfile
        from engine.scoring.engine import score_profile

        config = _load_config()
        profile_cfg = config.get("profile", {})
        demo = Demographics(
            age=profile_cfg.get("age", 35),
            sex=profile_cfg.get("sex", "M"),
        )
        profile = UserProfile(demographics=demo)

        # Load config-level health data
        if profile_cfg.get("family_history") is not None:
            profile.has_family_history = profile_cfg["family_history"]
        if profile_cfg.get("medications") is not None:
            profile.has_medication_list = True
        if profile_cfg.get("waist_inches") is not None:
            profile.waist_circumference = profile_cfg["waist_inches"]
        if profile_cfg.get("phq9_score") is not None:
            profile.phq9_score = profile_cfg["phq9_score"]

        # Load wearable data into profile (Garmin preferred, Apple Health fallback)
        data_dir = _data_dir()
        wearable_attrs = ("resting_hr", "daily_steps_avg", "sleep_regularity_stddev",
                          "sleep_duration_avg", "vo2_max", "hrv_rmssd_avg", "zone2_min_per_week")
        wearable_data = _load_json_file(data_dir / "garmin_latest.json")
        if wearable_data is None:
            wearable_data = _load_json_file(data_dir / "apple_health_latest.json")
        if wearable_data:
            for attr in wearable_attrs:
                val = wearable_data.get(attr)
                if val is not None:
                    setattr(profile, attr, val)

        bp_rows = read_csv(data_dir / "bp_log.csv")
        if bp_rows:
            profile.systolic = float(bp_rows[-1]["systolic"])
            profile.diastolic = float(bp_rows[-1]["diastolic"])

        weight_rows = read_csv(data_dir / "weight_log.csv")
        if weight_rows and weight_rows[-1].get("weight_lbs"):
            profile.weight_lbs = float(weight_rows[-1]["weight_lbs"])

        # Load lab results for scoring + clinical zones
        metric_dates = {}
        metric_counts = {}
        lab_path = data_dir / "lab_results.json"
        if lab_path.exists():
            import json as json_mod
            with open(lab_path) as f:
                labs = json_mod.load(f)
            latest = labs.get("latest", {})
            for key in ("ldl_c", "hdl_c", "triglycerides", "apob", "fasting_glucose",
                        "hba1c", "fasting_insulin", "hscrp", "alt", "ggt", "tsh",
                        "ferritin", "hemoglobin", "lpa"):
                val = latest.get(key)
                if val is not None:
                    setattr(profile, key, val)
            # Extract dates from draws
            for draw in labs.get("draws", []):
                draw_date = draw.get("date", "")
                for key in draw.get("results", {}):
                    if key not in metric_dates:
                        metric_dates[key] = draw_date
            # Count readings per metric
            for draw in labs.get("draws", []):
                for key in draw.get("results", {}):
                    metric_counts[key] = metric_counts.get(key, 0) + 1

        # Wearable dates
        if wearable_data:
            wearable_date = wearable_data.get("last_updated", "")[:10]
            if wearable_date:
                for key in wearable_attrs:
                    metric_dates[key] = wearable_date

        output = score_profile(profile, metric_dates=metric_dates,
                               metric_counts=metric_counts)
        return {
            "coverage_score": output["coverage_score"],
            "coverage_fraction": output["coverage_fraction"],
            "tier1_pct": output["tier1_pct"],
            "tier1_fraction": output["tier1_fraction"],
            "tier2_pct": output["tier2_pct"],
            "tier2_fraction": output["tier2_fraction"],
            "avg_percentile": output["avg_percentile"],
            "results": [r.to_dict() for r in output["results"] if r.has_data],
            "gaps": [
                {"name": g.name, "weight": g.coverage_weight, "cost": g.cost_to_close}
                for g in output["gaps"]
            ],
        }

    @mcp.tool()
    def get_protocols() -> list[dict]:
        """Active protocol progress — day, week, phase, last night's habits, nudges, outcomes. Covers sleep stack, nicotine taper, and any other active protocols."""
        from engine.coaching.protocols import load_protocol, protocol_progress

        config = _load_config()
        focus_list = config.get("focus", [])
        if not focus_list:
            return [{"message": "No active protocols in config.yaml focus list."}]

        data_dir = _data_dir()
        habit_data = read_csv(data_dir / "daily_habits.csv") or None

        garmin = None
        garmin_path = data_dir / "garmin_latest.json"
        if garmin_path.exists():
            import json
            with open(garmin_path) as f:
                garmin = json.load(f)

        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        for entry in focus_list:
            proto_name = entry.get("protocol")
            started = entry.get("started")
            if not proto_name or not started:
                continue
            proto = load_protocol(proto_name, protocols_dir=PROJECT_ROOT / "protocols")
            if not proto:
                results.append({"protocol": proto_name, "error": "Protocol file not found"})
                continue
            progress = protocol_progress(
                protocol=proto, started=started,
                habit_data=habit_data, garmin=garmin, as_of=today,
            )
            progress["priority"] = entry.get("priority", 99)
            results.append(progress)

        results.sort(key=lambda p: p.get("priority", 99))
        return results

    @mcp.tool()
    def log_weight(weight_lbs: float, date: str | None = None) -> dict:
        """Log a weight measurement. Date defaults to today."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        data_dir = _data_dir()
        path = data_dir / "weight_log.csv"
        rows = read_csv(path)
        fieldnames = ["date", "weight_lbs", "source", "waist_in"]
        rows.append({"date": date, "weight_lbs": str(weight_lbs), "source": "mcp", "waist_in": ""})
        write_csv(path, rows, fieldnames=fieldnames)
        return {"logged": True, "date": date, "weight_lbs": weight_lbs}

    @mcp.tool()
    def log_bp(systolic: int, diastolic: int, date: str | None = None) -> dict:
        """Log a blood pressure reading. Date defaults to today."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        data_dir = _data_dir()
        path = data_dir / "bp_log.csv"
        rows = read_csv(path)
        fieldnames = ["date", "systolic", "diastolic", "source"]
        rows.append({"date": date, "systolic": str(systolic), "diastolic": str(diastolic), "source": "mcp"})
        write_csv(path, rows, fieldnames=fieldnames)
        return {"logged": True, "date": date, "systolic": systolic, "diastolic": diastolic}

    @mcp.tool()
    def log_habits(habits: dict, date: str | None = None) -> dict:
        """Log daily habits. Pass a dict of habit_name: 'y' or 'n'. Date defaults to today. Habit names must match CSV columns (e.g. am_sunlight, creatine, evening_routine)."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        data_dir = _data_dir()
        path = data_dir / "daily_habits.csv"
        rows = read_csv(path)

        # Get fieldnames from existing data
        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = ["date"] + list(habits.keys()) + ["notes"]

        # Find or create row for this date
        target_row = None
        for row in rows:
            if row.get("date") == date:
                target_row = row
                break
        if target_row is None:
            target_row = {"date": date}
            rows.append(target_row)

        # Add any new habit columns
        for k in habits:
            if k not in fieldnames:
                fieldnames.insert(-1, k)  # before 'notes'

        # Update values
        for k, v in habits.items():
            target_row[k] = v

        write_csv(path, rows, fieldnames=fieldnames)
        return {"logged": True, "date": date, "habits": habits}

    @mcp.tool()
    def log_meal(
        description: str,
        protein_g: float,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        calories: float | None = None,
        date: str | None = None,
    ) -> dict:
        """Log a meal. Protein is required; carbs, fat, calories are optional. Date defaults to today."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        data_dir = _data_dir()
        path = data_dir / "meal_log.csv"
        rows = read_csv(path)
        fieldnames = ["date", "meal_num", "time_of_day", "description", "protein_g", "carbs_g", "fat_g", "calories", "notes"]

        # Compute meal_num for this date
        meals_today = [r for r in rows if r.get("date") == date]
        meal_num = len(meals_today) + 1

        hour = datetime.now().hour
        time_of_day = "AM" if hour < 12 else ("PM" if hour < 17 else "EVE")

        rows.append({
            "date": date,
            "meal_num": str(meal_num),
            "time_of_day": time_of_day,
            "description": description,
            "protein_g": str(protein_g),
            "carbs_g": str(carbs_g) if carbs_g is not None else "",
            "fat_g": str(fat_g) if fat_g is not None else "",
            "calories": str(calories) if calories is not None else "",
            "notes": "",
        })
        write_csv(path, rows, fieldnames=fieldnames)
        return {"logged": True, "date": date, "meal_num": meal_num, "description": description, "protein_g": protein_g}

    @mcp.tool()
    def get_status() -> dict:
        """Data files inventory — what exists, last modified, row counts. Useful for understanding what data the user has."""
        data_dir = _data_dir()
        files = {}
        for name in ["weight_log.csv", "bp_log.csv", "meal_log.csv", "daily_habits.csv",
                      "strength_log.csv", "garmin_latest.json", "garmin_daily.json",
                      "lab_results.json", "briefing.json"]:
            path = data_dir / name
            if path.exists():
                stat = path.stat()
                info = {
                    "exists": True,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_bytes": stat.st_size,
                }
                if name.endswith(".csv"):
                    rows = read_csv(path)
                    info["rows"] = len(rows)
                files[name] = info
            else:
                files[name] = {"exists": False}

        config = _load_config()
        has_config = bool(config.get("profile", {}).get("age"))
        return {"data_dir": str(data_dir), "config_loaded": has_config, "files": files}

    @mcp.tool()
    def onboard() -> dict:
        """Coverage map and guided setup. Shows all 20 health metrics,
        what's tracked vs missing, and ranked next steps by leverage.
        Call for new users or when someone asks 'what should I measure?'"""
        from engine.models import Demographics, UserProfile
        from engine.scoring.engine import score_profile

        config = _load_config()
        profile_cfg = config.get("profile", {})
        data_dir = _data_dir()

        # Build profile — same data loading as briefing/score
        has_config = bool(profile_cfg.get("age"))
        if has_config:
            demo = Demographics(
                age=profile_cfg.get("age", 35),
                sex=profile_cfg.get("sex", "M"),
            )
        else:
            demo = Demographics(age=35, sex="M")

        profile = UserProfile(demographics=demo)

        # Load config-level health data
        if profile_cfg.get("family_history") is not None:
            profile.has_family_history = profile_cfg["family_history"]
        if profile_cfg.get("medications") is not None:
            profile.has_medication_list = True
        if profile_cfg.get("waist_inches") is not None:
            profile.waist_circumference = profile_cfg["waist_inches"]
        if profile_cfg.get("phq9_score") is not None:
            profile.phq9_score = profile_cfg["phq9_score"]

        # Load wearable data (Garmin preferred, Apple Health fallback)
        garmin_path = data_dir / "garmin_latest.json"
        wearable_data = _load_json_file(garmin_path)
        if wearable_data is None:
            wearable_data = _load_json_file(data_dir / "apple_health_latest.json")
        if wearable_data:
            for attr in ("resting_hr", "daily_steps_avg", "sleep_regularity_stddev",
                         "sleep_duration_avg", "vo2_max", "hrv_rmssd_avg", "zone2_min_per_week"):
                val = wearable_data.get(attr)
                if val is not None:
                    setattr(profile, attr, val)

        # Load BP
        bp_rows = read_csv(data_dir / "bp_log.csv")
        if bp_rows:
            profile.systolic = float(bp_rows[-1]["systolic"])
            profile.diastolic = float(bp_rows[-1]["diastolic"])

        # Load weight
        weight_rows = read_csv(data_dir / "weight_log.csv")
        if weight_rows and weight_rows[-1].get("weight_lbs"):
            profile.weight_lbs = float(weight_rows[-1]["weight_lbs"])

        # Load labs
        lab_path = data_dir / "lab_results.json"
        if lab_path.exists():
            import json
            with open(lab_path) as f:
                labs = json.load(f)
            latest = labs.get("latest", {})
            for key in ("ldl_c", "hdl_c", "triglycerides", "apob", "fasting_glucose",
                        "hba1c", "fasting_insulin", "hscrp", "alt", "ggt", "tsh",
                        "ferritin", "hemoglobin", "lpa"):
                val = latest.get(key)
                if val is not None:
                    setattr(profile, key, val)

        # Score
        output = score_profile(profile)
        total_weight = sum(r.coverage_weight for r in output["results"])

        # Build coverage map — ALL 20 metrics
        coverage_map = []
        for r in output["results"]:
            entry = {
                "name": r.name,
                "tier": r.tier,
                "weight": r.coverage_weight,
                "weight_pct": round(r.coverage_weight / total_weight * 100, 1),
            }
            if r.has_data and r.value is not None:
                entry["status"] = "scored"
                entry["value"] = r.value
                entry["unit"] = r.unit
                entry["standing"] = r.standing.value
                entry["percentile"] = r.percentile_approx
            elif r.has_data:
                entry["status"] = "collected"
                entry["standing"] = r.standing.value
            else:
                entry["status"] = "missing"
                entry["cost"] = r.cost_to_close
                entry["why"] = r.note
            coverage_map.append(entry)

        # Next steps — top 5 gaps with coverage boost
        next_steps = []
        for g in output["gaps"][:5]:
            next_steps.append({
                "name": g.name,
                "tier": g.tier,
                "weight": g.coverage_weight,
                "coverage_boost": round(g.coverage_weight / total_weight * 100, 1),
                "cost": g.cost_to_close,
                "why": g.note,
            })

        # Detect which data sources exist
        data_sources = {}
        for name in ["garmin_latest.json", "apple_health_latest.json",
                      "bp_log.csv", "weight_log.csv",
                      "meal_log.csv", "daily_habits.csv", "lab_results.json",
                      "strength_log.csv"]:
            data_sources[name] = (data_dir / name).exists()

        # Wearable connection status
        from engine.integrations.garmin import GarminClient
        garmin_cfg = config.get("garmin", {})
        garmin_token_dir = garmin_cfg.get("token_dir")
        garmin_tokens = GarminClient.has_tokens(token_dir=garmin_token_dir)
        garmin_has_data = garmin_path.exists()
        garmin_freshness = None
        if garmin_has_data:
            garmin_json = _load_json_file(garmin_path)
            if garmin_json:
                garmin_freshness = garmin_json.get("last_updated")

        if not garmin_tokens:
            garmin_hint = "Run `python3 cli.py auth garmin` to authenticate."
        elif not garmin_has_data:
            garmin_hint = "Tokens cached. Run `python3 cli.py pull garmin` to fetch data."
        else:
            garmin_hint = "Connected. Pull to refresh."

        ah_has_data = (data_dir / "apple_health_latest.json").exists()
        ah_freshness = None
        if ah_has_data:
            ah_data = _load_json_file(data_dir / "apple_health_latest.json")
            if ah_data:
                ah_freshness = ah_data.get("last_updated")

        wearables = {
            "garmin": {
                "tokens_cached": garmin_tokens,
                "has_data": garmin_has_data,
                "freshness": garmin_freshness,
                "connect_hint": garmin_hint,
            },
            "apple_health": {
                "has_data": ah_has_data,
                "freshness": ah_freshness,
                "connect_hint": (
                    "Connected." if ah_has_data else
                    "Export from iPhone: Settings → Health → Export Health Data. "
                    "Then run `python3 cli.py import apple-health /path/to/export.zip`."
                ),
            },
        }

        return {
            "profile": {
                "age": profile_cfg.get("age"),
                "sex": profile_cfg.get("sex"),
                "configured": has_config,
                "family_history": profile_cfg.get("family_history"),
                "medications": profile_cfg.get("medications"),
                "waist_inches": profile_cfg.get("waist_inches"),
                "phq9_score": profile_cfg.get("phq9_score"),
            },
            "coverage_score": output["coverage_score"],
            "tier1_pct": output["tier1_pct"],
            "tier2_pct": output["tier2_pct"],
            "coverage_map": coverage_map,
            "next_steps": next_steps,
            "data_sources_detected": data_sources,
            "wearables": wearables,
            "interests_hint": (
                "Ask the user what they care about most — heart health, fitness, "
                "metabolic health, longevity, mental health — to personalize which "
                "gaps to prioritize. Not everyone needs all 20 metrics."
            ),
        }

    @mcp.tool()
    def connect_garmin() -> dict:
        """Check Garmin connection status — whether tokens are cached, data freshness, and hints for next steps."""
        from engine.integrations.garmin import GarminClient

        config = _load_config()
        garmin_cfg = config.get("garmin", {})
        token_dir = garmin_cfg.get("token_dir")
        has_tokens = GarminClient.has_tokens(token_dir=token_dir)

        data_dir = _data_dir()
        garmin_path = data_dir / "garmin_latest.json"
        has_data = garmin_path.exists()
        freshness = None
        if has_data:
            import json
            with open(garmin_path) as f:
                garmin = json.load(f)
            freshness = garmin.get("last_updated")

        if not has_tokens:
            hint = "No Garmin tokens found. Run `python3 cli.py auth garmin` to authenticate."
        elif not has_data:
            hint = "Tokens cached but no data yet. Run `python3 cli.py pull garmin` to fetch metrics."
        else:
            hint = "Connected. Run `python3 cli.py pull garmin` to refresh data."

        return {
            "tokens_cached": has_tokens,
            "has_data": has_data,
            "last_updated": freshness,
            "hint": hint,
        }

    @mcp.tool()
    def open_dashboard() -> dict:
        """Open the health dashboard in a browser. Refreshes briefing data first."""
        from engine.coaching.briefing import build_briefing

        config = _load_config()
        briefing = build_briefing(config)

        # Write fresh briefing for dashboard to read
        data_dir = _data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        briefing_path = data_dir / "briefing.json"
        with open(briefing_path, "w") as f:
            json.dump(briefing, f, indent=2)

        # Open dashboard
        dashboard_path = PROJECT_ROOT / "dashboard" / "index.html"
        if not dashboard_path.exists():
            return {"opened": False, "error": "dashboard/index.html not found"}

        webbrowser.open(f"file://{dashboard_path.resolve()}")
        return {"opened": True, "briefing_refreshed": True}

    @mcp.tool()
    def setup_profile(
        age: int,
        sex: str,
        weight_target: float | None = None,
        protein_target: float | None = None,
        family_history: bool | None = None,
        medications: str | None = None,
        waist_inches: float | None = None,
        phq9_score: int | None = None,
    ) -> dict:
        """Create or update config.yaml with user profile. Sex should be 'M' or 'F'. Weight target in lbs, protein in grams. family_history: any heart disease before 60 in parents? medications: free text list. waist_inches: waist measurement. phq9_score: 0-27 depression screen."""
        cp = _config_path()
        cp.parent.mkdir(parents=True, exist_ok=True)

        if cp.exists():
            with open(cp) as f:
                config = yaml.safe_load(f) or {}
        else:
            # Start from example if available (local dev)
            example = PROJECT_ROOT / "config.example.yaml"
            if example.exists():
                with open(example) as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}

        config.setdefault("profile", {})
        config["profile"]["age"] = age
        config["profile"]["sex"] = sex.upper()
        if family_history is not None:
            config["profile"]["family_history"] = family_history
        if medications is not None:
            config["profile"]["medications"] = medications
        if waist_inches is not None:
            config["profile"]["waist_inches"] = waist_inches
        if phq9_score is not None:
            config["profile"]["phq9_score"] = phq9_score

        config.setdefault("targets", {})
        if weight_target is not None:
            config["targets"]["weight_lbs"] = weight_target
        if protein_target is not None:
            config["targets"]["protein_g"] = protein_target

        with open(cp, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        # Ensure data dir exists
        data_dir = _data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        return {
            "saved": True,
            "config_path": str(cp),
            "profile": config["profile"],
            "targets": config["targets"],
        }


def register_resources(mcp: FastMCP):
    """Register MCP resources (readable documents)."""

    @mcp.resource("health-engine://methodology")
    def methodology() -> str:
        """Full scoring methodology — why each metric is measured, evidence sources, clinical thresholds, freshness decay, reliability multipliers."""
        # Try local docs first (dev), then packaged copy
        methodology_path = PROJECT_ROOT / "docs" / "METHODOLOGY.md"
        if not methodology_path.exists():
            methodology_path = Path(__file__).parent.parent / "engine" / "data" / "METHODOLOGY.md"
        if not methodology_path.exists():
            return "METHODOLOGY.md not found."
        return methodology_path.read_text()
