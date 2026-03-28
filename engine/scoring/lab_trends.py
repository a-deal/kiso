"""Lab trend tracking across draws.

Compares the two most recent values for each marker, flags clinically
significant changes, and suggests retest timing.
"""

from datetime import datetime
from typing import Optional


# Clinically significant change thresholds per marker
# From the timescale framework research doc
SIGNIFICANT_THRESHOLDS = {
    "ldl_c": {"delta": 15, "unit": "mg/dL", "direction": "lower_better"},
    "hdl_c": {"delta": 10, "unit": "mg/dL", "direction": "higher_better"},
    "triglycerides": {"delta_pct": 0.30, "unit": "mg/dL", "direction": "lower_better"},
    "apob": {"delta": 10, "unit": "mg/dL", "direction": "lower_better"},
    "total_cholesterol": {"delta": 20, "unit": "mg/dL", "direction": "lower_better"},
    "fasting_glucose": {"delta": 10, "unit": "mg/dL", "direction": "lower_better"},
    "hba1c": {"delta": 0.5, "unit": "%", "direction": "lower_better"},
    "fasting_insulin": {"delta": 3, "unit": "uIU/mL", "direction": "lower_better"},
    "hscrp": {"delta": 1.0, "unit": "mg/L", "direction": "lower_better"},
    "tsh": {"delta": 1.0, "unit": "mIU/L", "direction": "neutral"},
    "testosterone_total": {"delta": 100, "unit": "ng/dL", "direction": "higher_better"},
    "testosterone_free": {"delta": 20, "unit": "pg/mL", "direction": "higher_better"},
    "shbg": {"delta": 10, "unit": "nmol/L", "direction": "neutral"},
    "ferritin": {"delta": 30, "unit": "ng/mL", "direction": "neutral"},
    "hemoglobin": {"delta": 1.0, "unit": "g/dL", "direction": "neutral"},
    "ggt": {"delta": 15, "unit": "U/L", "direction": "lower_better"},
    "alt": {"delta": 15, "unit": "U/L", "direction": "lower_better"},
    "ast": {"delta": 15, "unit": "U/L", "direction": "lower_better"},
    "vitamin_d": {"delta": 10, "unit": "ng/mL", "direction": "higher_better"},
    "fsh": {"delta": 5, "unit": "mIU/mL", "direction": "neutral"},
    "lh": {"delta": 3, "unit": "mIU/mL", "direction": "neutral"},
    "dhea_s": {"delta": 50, "unit": "ug/dL", "direction": "higher_better"},
    "homocysteine": {"delta": 3, "unit": "umol/L", "direction": "lower_better"},
    "omega3_index": {"delta": 1.0, "unit": "%", "direction": "higher_better"},
    "iron_saturation_pct": {"delta": 10, "unit": "%", "direction": "neutral"},
}

# Recommended retest cadence in months
RETEST_CADENCE = {
    "ldl_c": 3, "hdl_c": 6, "triglycerides": 3, "apob": 3,
    "total_cholesterol": 6, "fasting_glucose": 3, "hba1c": 3,
    "fasting_insulin": 6, "hscrp": 6, "tsh": 6,
    "testosterone_total": 3, "testosterone_free": 3, "shbg": 6,
    "ferritin": 6, "hemoglobin": 12, "ggt": 6, "alt": 6, "ast": 6,
    "vitamin_d": 3, "fsh": 6, "lh": 6, "dhea_s": 12,
    "homocysteine": 12, "omega3_index": 6, "iron_saturation_pct": 12,
}

# One-time tests (don't suggest retest)
ONE_TIME = {"lpa"}


def compute_lab_trends(labs: dict) -> dict:
    """Compare most recent two draws for each marker.

    Args:
        labs: Lab results dict with 'draws' (list of {date, results}) 
              and 'latest' (flat dict of most recent values).

    Returns:
        Dict with per-marker trends:
        {
            "markers": {
                "testosterone_total": {
                    "current": 664,
                    "previous": 583,
                    "delta": 81,
                    "delta_pct": 13.9,
                    "significant": false,
                    "direction": "improving",
                    "current_date": "2026-02-13",
                    "previous_date": "2025-06-02",
                    "days_between": 256,
                    "retest_months": 3,
                    "retest_due": "2026-05-13",
                },
                ...
            },
            "significant_changes": [...],
            "retest_due": [...],
            "summary": "..."
        }
    """
    draws = labs.get("draws", [])
    if not draws:
        return {}

    # Build per-marker history: list of (date, value) sorted newest first
    marker_history = {}
    for draw in draws:
        draw_date = draw.get("date", "")
        for marker, value in draw.get("results", {}).items():
            if value is not None:
                if marker not in marker_history:
                    marker_history[marker] = []
                marker_history[marker].append((draw_date, value))

    markers = {}
    significant_changes = []
    retest_due = []
    today = datetime.now().strftime("%Y-%m-%d")

    for marker, history in marker_history.items():
        # Sort by date descending
        history.sort(key=lambda x: x[0], reverse=True)
        current_date, current_val = history[0]

        entry = {
            "current": current_val,
            "current_date": current_date,
        }

        # Compare to previous if exists
        if len(history) >= 2:
            prev_date, prev_val = history[1]
            try:
                curr_f = float(current_val)
                prev_f = float(prev_val)
                delta = curr_f - prev_f
                delta_pct = (delta / prev_f * 100) if prev_f != 0 else 0

                entry["previous"] = prev_val
                entry["previous_date"] = prev_date
                entry["delta"] = round(delta, 1)
                entry["delta_pct"] = round(delta_pct, 1)

                # Days between draws
                try:
                    d1 = datetime.strptime(current_date, "%Y-%m-%d")
                    d2 = datetime.strptime(prev_date, "%Y-%m-%d")
                    entry["days_between"] = (d1 - d2).days
                except ValueError:
                    pass

                # Check significance
                threshold = SIGNIFICANT_THRESHOLDS.get(marker)
                if threshold:
                    direction_pref = threshold.get("direction", "neutral")
                    abs_delta = abs(delta)

                    is_significant = False
                    if "delta" in threshold:
                        is_significant = abs_delta >= threshold["delta"]
                    elif "delta_pct" in threshold:
                        is_significant = abs(delta_pct / 100) >= threshold["delta_pct"]

                    entry["significant"] = is_significant

                    # Determine if change is improving or worsening
                    if direction_pref == "lower_better":
                        entry["direction"] = "improving" if delta < 0 else "worsening"
                    elif direction_pref == "higher_better":
                        entry["direction"] = "improving" if delta > 0 else "worsening"
                    else:
                        entry["direction"] = "changed" if is_significant else "stable"

                    if is_significant:
                        unit = threshold.get("unit", "")
                        significant_changes.append({
                            "marker": marker,
                            "delta": round(delta, 1),
                            "unit": unit,
                            "direction": entry["direction"],
                            "current": current_val,
                            "previous": prev_val,
                            "current_date": current_date,
                            "previous_date": prev_date,
                        })
            except (ValueError, TypeError):
                pass

        # Retest timing
        if marker not in ONE_TIME:
            cadence = RETEST_CADENCE.get(marker, 6)
            entry["retest_months"] = cadence
            try:
                last_draw = datetime.strptime(current_date, "%Y-%m-%d")
                from dateutil.relativedelta import relativedelta
                retest_date = last_draw + relativedelta(months=cadence)
                entry["retest_due"] = retest_date.strftime("%Y-%m-%d")
                if retest_date.strftime("%Y-%m-%d") <= today:
                    retest_due.append({
                        "marker": marker,
                        "last_draw": current_date,
                        "due": retest_date.strftime("%Y-%m-%d"),
                        "overdue_days": (datetime.now() - retest_date).days,
                    })
            except (ValueError, ImportError):
                # dateutil not available, use rough calculation
                from datetime import timedelta
                retest_date = datetime.strptime(current_date, "%Y-%m-%d") + timedelta(days=cadence * 30)
                entry["retest_due"] = retest_date.strftime("%Y-%m-%d")
                if retest_date.strftime("%Y-%m-%d") <= today:
                    retest_due.append({
                        "marker": marker,
                        "last_draw": current_date,
                        "due": retest_date.strftime("%Y-%m-%d"),
                    })

        markers[marker] = entry

    # Sort significant changes by absolute delta_pct descending
    significant_changes.sort(key=lambda x: abs(x.get("delta", 0)), reverse=True)

    # Sort retest due by overdue days descending
    retest_due.sort(key=lambda x: x.get("overdue_days", 0), reverse=True)

    result = {
        "markers": markers,
        "significant_changes": significant_changes[:5],  # top 5
        "retest_due": retest_due[:5],  # top 5 most overdue
        "total_markers": len(markers),
        "total_draws": len(draws),
    }

    return result
