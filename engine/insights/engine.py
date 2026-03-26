"""Health insights engine — generates actionable insights from wearable + health data.

Ported from dashboard.js generateInsights() and generalized.
All thresholds are configurable via rules.yaml.
"""

from pathlib import Path
from typing import Optional

import yaml

from engine.models import Insight

_DEFAULT_RULES_PATH = Path(__file__).parent / "rules.yaml"


def load_rules(path: Optional[str] = None, user_id: Optional[str] = None) -> dict:
    """Load threshold rules from YAML file, with optional per-user overrides.

    If user_id is provided, looks for data/users/<user_id>/rules.yaml
    and merges those values on top of the defaults. This allows per-user
    threshold calibration (e.g., different RHR thresholds for an athlete
    vs a sedentary 42-year-old).
    """
    p = Path(path) if path else _DEFAULT_RULES_PATH
    with open(p) as f:
        rules = yaml.safe_load(f)

    if user_id:
        # Resolve from the repo root (3 levels up from engine/insights/engine.py)
        repo_root = Path(__file__).resolve().parent.parent.parent
        user_rules_path = repo_root / "data" / "users" / user_id / "rules.yaml"
        if user_rules_path.exists():
            with open(user_rules_path) as f:
                overrides = yaml.safe_load(f) or {}
            # Deep merge: override individual thresholds, not entire sections
            for section, values in overrides.items():
                if section in rules and isinstance(values, dict):
                    rules[section].update(values)
                else:
                    rules[section] = values

    return rules


def generate_insights(
    garmin: Optional[dict] = None,
    weights: Optional[list] = None,
    bp_readings: Optional[list] = None,
    trends: Optional[dict] = None,
    rules: Optional[dict] = None,
) -> list[Insight]:
    """
    Generate health insights from available data.

    Args:
        garmin: Dict with keys like hrv_rmssd_avg, resting_hr, sleep_duration_avg,
                sleep_regularity_stddev, zone2_min_per_week
        weights: List of dicts with 'weight' key, chronologically ordered
        bp_readings: List of dicts with 'sys' and 'dia' keys
        trends: Dict with 'rhr_pts' and 'hrv_pts' lists (each item has date + value)
        rules: Threshold rules dict (loaded from rules.yaml if not provided)

    Returns:
        List of Insight objects
    """
    if rules is None:
        rules = load_rules()

    insights = []
    g = garmin or {}
    hrv = g.get("hrv_rmssd_avg")
    rhr = g.get("resting_hr")
    sleep_hrs = g.get("sleep_duration_avg")
    sleep_reg = g.get("sleep_regularity_stddev")
    zone2 = g.get("zone2_min_per_week")

    r_hrv = rules.get("hrv", {})
    r_rhr = rules.get("rhr", {})
    r_sleep = rules.get("sleep", {})
    r_zone2 = rules.get("zone2", {})
    r_weight = rules.get("weight", {})
    r_bp = rules.get("bp", {})

    # Compute trends
    rhr_trend = _compute_trend(trends, "rhr_pts", "rhr", 14)
    hrv_trend = _compute_trend(trends, "hrv_pts", "hrv", 14)

    # Weekly weight loss rate
    weekly_rate = None
    if weights and len(weights) >= 7:
        recent = weights[-1]["weight"]
        week_ago = weights[max(0, len(weights) - 8)]["weight"]
        weekly_rate = week_ago - recent

    # --- HRV ---
    if hrv is not None:
        hrv_trend_note = _format_trend_note(hrv_trend, "ms", "Recovery capacity", "Accumulated fatigue")

        if hrv < r_hrv.get("critical_low", 50):
            insights.append(Insight(
                severity="critical", category="hrv",
                title=f"HRV below {r_hrv.get('critical_low', 50)}ms — recovery warning",
                body=f"Sustained HRV below {r_hrv.get('critical_low', 50)}ms signals overreaching. "
                     f"Consider a refeed day, extra sleep, or reducing training volume.{hrv_trend_note}",
            ))
        elif hrv < r_hrv.get("warning_low", 55):
            insights.append(Insight(
                severity="warning", category="hrv",
                title=f"HRV approaching warning zone ({hrv:.1f}ms)",
                body=f"Getting close to the {r_hrv.get('critical_low', 50)}ms threshold. "
                     f"Sleep duration ({sleep_hrs or '?'}hrs) is likely the primary driver. "
                     f"Prioritize sleep over training intensity this week.{hrv_trend_note}",
            ))
        elif hrv >= r_hrv.get("healthy_high", 65):
            insights.append(Insight(
                severity="positive", category="hrv",
                title=f"HRV solid at {hrv:.1f}ms",
                body=f"Parasympathetic tone is healthy. Recovery capacity is good — "
                     f"you can maintain current training load and deficit.{hrv_trend_note}",
            ))
        else:
            insights.append(Insight(
                severity="neutral", category="hrv",
                title=f"HRV at {hrv:.1f}ms — mid-range",
                body=f"Within normal range for someone in a caloric deficit.{hrv_trend_note}",
            ))

    # --- Sleep + HRV interaction ---
    if sleep_hrs is not None and sleep_hrs < r_sleep.get("duration_target", 7.0):
        if hrv is not None and hrv < 60:
            insights.append(Insight(
                severity="warning", category="sleep",
                title="Sleep deficit dragging HRV down",
                body=f"Averaging {sleep_hrs:.1f} hrs — below the {r_sleep.get('duration_target', 7.0)}hr target. "
                     f"This is likely suppressing your HRV. Sleep is the #1 lever for recovery during a deficit. "
                     f"An extra 30-45 min of sleep would likely move HRV more than any supplement.",
            ))
        else:
            insights.append(Insight(
                severity="warning", category="sleep",
                title=f"Sleep below target ({sleep_hrs:.1f} hrs)",
                body=f"Averaging {sleep_hrs:.1f} hrs — below the {r_sleep.get('duration_target', 7.0)}hr target. "
                     f"Recovery markers are holding for now, but chronic sub-{r_sleep.get('duration_target', 7.0)}hr "
                     f"sleep compounds over weeks. Fixed wake time + earlier wind-down are higher leverage than sleep supplements.",
            ))

    # --- Sleep regularity ---
    if sleep_reg is not None and sleep_reg > r_sleep.get("regularity_high", 60):
        insights.append(Insight(
            severity="warning", category="sleep",
            title=f"Bedtime variance high (±{round(sleep_reg)} min)",
            body=f"Irregular sleep timing disrupts circadian rhythm independent of duration. "
                 f"A consistent wake time (even on weekends) is the single most effective fix. "
                 f"Aim for <{r_sleep.get('regularity_target', 45)} min stdev.",
        ))

    # --- RHR ---
    if rhr is not None:
        rhr_trend_note = _format_trend_note(rhr_trend, "bpm", "Aerobic fitness", "Accumulated fatigue", invert=True)

        if rhr > r_rhr.get("elevated", 55):
            insights.append(Insight(
                severity="critical", category="rhr",
                title=f"Resting HR elevated ({rhr:.1f} bpm)",
                body=f"RHR above {r_rhr.get('elevated', 55)} during a cut signals systemic stress. "
                     f"Consider a diet break or deload week.{rhr_trend_note}",
            ))
        elif rhr < r_rhr.get("excellent", 50):
            insights.append(Insight(
                severity="positive", category="rhr",
                title=f"Resting HR excellent ({rhr:.1f} bpm)",
                body=f"Sub-{r_rhr.get('excellent', 50)} RHR indicates strong cardiovascular fitness and adequate recovery.{rhr_trend_note}",
            ))
        else:
            insights.append(Insight(
                severity="neutral", category="rhr",
                title=f"Resting HR at {rhr:.1f} bpm — normal range",
                body=f"Within healthy range.{rhr_trend_note}",
            ))

    # --- Zone 2 ---
    if zone2 is not None:
        target = r_zone2.get("target_min_per_week", 150)
        low = r_zone2.get("low_threshold", 90)
        if zone2 >= target:
            insights.append(Insight(
                severity="positive", category="zone2",
                title=f"Zone 2 strong at {zone2} min/week",
                body=f"Well above the {target} min/week recommendation. "
                     f"This is protective for cardiovascular health. "
                     f"Zone 2 also supports fat oxidation during a cut.",
            ))
        elif zone2 < low:
            insights.append(Insight(
                severity="warning", category="zone2",
                title=f"Zone 2 low ({zone2} min/week)",
                body=f"Below {target} min/week target. Even 2-3 brisk walks per week would help. "
                     f"Zone 2 supports both cardiac health and metabolic flexibility.",
            ))

    # --- Weight rate + recovery interaction ---
    if weekly_rate is not None and weekly_rate > r_weight.get("fast_loss_threshold", 2.0):
        if hrv is not None and hrv < r_hrv.get("warning_low", 55):
            insights.append(Insight(
                severity="critical", category="weight",
                title="Fast loss + low HRV — consider slowing down",
                body=f"Losing {weekly_rate:.1f} lbs/week with HRV at {hrv:.1f}ms. "
                     f"The deficit may be too aggressive for your current recovery capacity. "
                     f"A +200-300 cal bump would slow the rate while preserving muscle and gym performance.",
            ))

    # --- Blood pressure ---
    if bp_readings and len(bp_readings) > 0:
        last_bp = bp_readings[-1]
        sys_opt = r_bp.get("systolic_optimal", 120)
        dia_opt = r_bp.get("diastolic_optimal", 80)
        sys_elev = r_bp.get("systolic_elevated", 130)

        if last_bp["sys"] < sys_opt and last_bp["dia"] < dia_opt:
            insights.append(Insight(
                severity="positive", category="bp",
                title=f"Blood pressure normal ({last_bp['sys']}/{last_bp['dia']})",
                body=f"Optimal range. Continue monitoring — BP tends to improve with weight loss. "
                     f"Each 2 lbs lost typically drops systolic ~1 mmHg.",
            ))
        elif last_bp["sys"] >= sys_elev or last_bp["dia"] >= dia_opt:
            insights.append(Insight(
                severity="warning", category="bp",
                title=f"Blood pressure elevated ({last_bp['sys']}/{last_bp['dia']})",
                body=f"Above optimal range. Continue daily monitoring to establish a reliable baseline.",
            ))

    return insights


def _compute_trend(trends: Optional[dict], key: str, value_key: str, window: int) -> Optional[dict]:
    """Compute early vs late average for a trend series."""
    if not trends:
        return None
    pts = trends.get(key)
    if not pts or len(pts) < window:
        return None
    early = sum(p[value_key] for p in pts[:window]) / window
    late = sum(p[value_key] for p in pts[-window:]) / window
    return {"early": early, "late": late, "delta": late - early}


def _format_trend_note(trend: Optional[dict], unit: str,
                       improving_label: str, declining_label: str,
                       invert: bool = False) -> str:
    """Format a 90-day trend note for an insight body."""
    if not trend:
        return ""

    delta = trend["delta"]
    threshold = 3 if unit == "ms" else 2

    # For RHR, declining is good (invert=True)
    if invert:
        if delta < -threshold:
            return (f" 90-day trend: {trend['early']:.1f} → {trend['late']:.1f} {unit} "
                    f"(↓{abs(delta):.1f}). {improving_label} is improving.")
        elif delta > threshold:
            return (f" 90-day trend: {trend['early']:.1f} → {trend['late']:.1f} {unit} "
                    f"(↑{delta:.1f}). {declining_label} may be building — watch closely.")
        else:
            return (f" 90-day trend stable: {trend['early']:.1f} → {trend['late']:.1f} {unit}. "
                    f"Holding steady.")
    else:
        if delta > threshold:
            return (f" 90-day trend is positive: {trend['early']:.0f} → {trend['late']:.0f}{unit} "
                    f"(+{delta:.1f}). {improving_label} is building.")
        elif delta < -threshold:
            return (f" 90-day trend is declining: {trend['early']:.0f} → {trend['late']:.0f}{unit} "
                    f"({delta:.1f}). {declining_label} may be building — watch closely.")
        else:
            return (f" 90-day trend is stable: {trend['early']:.0f} → {trend['late']:.0f}{unit}. "
                    f"Holding steady.")
