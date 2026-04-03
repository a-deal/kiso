"""Behavior change measurement loop.

Links coaching messages to measurable outcomes by recording hypotheses
with 7-day baselines and computing deltas after 24 hours.
"""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger("health-engine.coaching.outcomes")

# Numeric columns in wearable_daily that can serve as metric targets.
VALID_METRIC_KEYS = frozenset({
    "rhr", "hrv", "steps", "sleep_hrs", "deep_sleep_hrs", "light_sleep_hrs",
    "rem_sleep_hrs", "awake_hrs", "calories_total", "calories_active",
    "calories_bmr", "stress_avg", "floors", "distance_m", "max_hr", "min_hr",
    "vo2_max", "body_battery", "zone2_min",
})


def _compute_baseline(db: sqlite3.Connection, person_id: str, metric_key: str) -> float | None:
    """Average of the most recent 7 days of wearable_daily for the given metric."""
    row = db.execute(
        f"SELECT AVG(val) FROM ("
        f"  SELECT {metric_key} AS val FROM wearable_daily "
        f"  WHERE person_id = ? AND {metric_key} IS NOT NULL "
        f"  ORDER BY date DESC LIMIT 7"
        f")",
        (person_id,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def record_hypothesis(
    db: sqlite3.Connection,
    person_id: str,
    hypothesis: str,
    metric_key: str,
    scheduled_send_id: int | None = None,
) -> dict:
    """Record a behavior change hypothesis with its 7-day baseline.

    Returns the inserted row as a dict.
    """
    if metric_key not in VALID_METRIC_KEYS:
        raise ValueError(f"metric_key '{metric_key}' not in wearable_daily. Valid: {sorted(VALID_METRIC_KEYS)}")

    baseline = _compute_baseline(db, person_id, metric_key)
    now = datetime.utcnow().isoformat(timespec="seconds")

    cursor = db.execute(
        "INSERT INTO coaching_outcome (person_id, scheduled_send_id, hypothesis, "
        "metric_key, baseline_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, scheduled_send_id, hypothesis, metric_key, baseline, now),
    )
    db.commit()

    row_id = cursor.lastrowid
    return {
        "id": row_id,
        "person_id": person_id,
        "scheduled_send_id": scheduled_send_id,
        "hypothesis": hypothesis,
        "metric_key": metric_key,
        "baseline_value": baseline,
        "created_at": now,
        "measured_at": None,
        "measured_value": None,
        "delta": None,
    }


def measure_outcomes(db: sqlite3.Connection, person_id: str | None = None) -> list[dict]:
    """Measure all unmeasured hypotheses older than 24 hours.

    For each eligible outcome, looks up the most recent wearable_daily value
    for that metric after the hypothesis was created. If data exists, computes
    delta = measured_value - baseline_value and writes it back.

    Returns list of outcomes that were measured in this call.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat(timespec="seconds")

    query = (
        "SELECT id, person_id, metric_key, baseline_value, created_at "
        "FROM coaching_outcome WHERE measured_at IS NULL AND created_at <= ?"
    )
    params: list = [cutoff]

    if person_id is not None:
        query += " AND person_id = ?"
        params.append(person_id)

    pending = db.execute(query, params).fetchall()
    measured = []

    now = datetime.utcnow().isoformat(timespec="seconds")

    for row in pending:
        oid, pid, metric_key, baseline, created_at = row

        # Get the most recent value for this metric from after the hypothesis date.
        # We use >= created_date + 1 day to ensure we only measure post-intervention data.
        hypothesis_date = created_at[:10]  # YYYY-MM-DD
        val_row = db.execute(
            f"SELECT {metric_key} FROM wearable_daily "
            f"WHERE person_id = ? AND date > ? AND {metric_key} IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (pid, hypothesis_date),
        ).fetchone()

        if val_row is None or val_row[0] is None:
            continue

        measured_value = val_row[0]
        delta = (measured_value - baseline) if baseline is not None else None

        db.execute(
            "UPDATE coaching_outcome SET measured_at = ?, measured_value = ?, delta = ? WHERE id = ?",
            (now, measured_value, delta, oid),
        )

        measured.append({
            "id": oid,
            "person_id": pid,
            "metric_key": metric_key,
            "baseline_value": baseline,
            "measured_value": measured_value,
            "delta": delta,
            "measured_at": now,
        })

    if measured:
        db.commit()
        logger.info("Measured %d coaching outcomes", len(measured))

    return measured


def get_outcomes(db: sqlite3.Connection, person_id: str, days: int = 30) -> list[dict]:
    """Return coaching outcomes for a person within the last N days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")

    rows = db.execute(
        "SELECT id, person_id, scheduled_send_id, hypothesis, metric_key, "
        "baseline_value, created_at, measured_at, measured_value, delta "
        "FROM coaching_outcome WHERE person_id = ? AND created_at >= ? "
        "ORDER BY created_at DESC",
        (person_id, cutoff),
    ).fetchall()

    return [
        {
            "id": r[0],
            "person_id": r[1],
            "scheduled_send_id": r[2],
            "hypothesis": r[3],
            "metric_key": r[4],
            "baseline_value": r[5],
            "created_at": r[6],
            "measured_at": r[7],
            "measured_value": r[8],
            "delta": r[9],
        }
        for r in rows
    ]


_CSV_FIELDS = [
    "id", "person_id", "hypothesis", "metric_key",
    "baseline_value", "created_at", "measured_at", "measured_value", "delta",
]


def export_outcomes_csv(db: sqlite3.Connection, person_id: str, days: int = 30) -> str:
    """Export coaching outcomes as a CSV string for weekly review."""
    outcomes = get_outcomes(db, person_id, days=days)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in outcomes:
        writer.writerow(row)
    return buf.getvalue()
