#!/usr/bin/env python3
"""Parse /health/deep JSON and emit alert lines for stale critical files.

Reads JSON from stdin, writes one alert line per user with a non-empty
stale_critical_files field. Absence of output = no alerts.

Used by scripts/healthcheck_gateway.sh to wire the per-file freshness
tripwire (added to deep_health_check() in commit 772d64c) to the
WhatsApp alerting path. See hub/plans/2026-04-12-baseline-consolidation.md
Milestone 6 for the broader freshness unification plan.
"""

import json
import sys


def format_stale_critical_alerts(deep_response: dict) -> list[str]:
    """Extract alert lines for users with stale_critical_files.

    Args:
        deep_response: parsed /health/deep response body

    Returns:
        List of alert strings, one per user with stale critical files.
        Empty list if no user has the field populated.

    Note: this targets the per-file tripwire specifically. Users who are
    stale by the aggregate signal alone (no stale_critical_files) are
    intentionally not included — that's a separate alert concern.
    """
    alerts: list[str] = []
    user_data = deep_response.get("checks", {}).get("user_data", {})
    if not isinstance(user_data, dict):
        return alerts
    for uid, entry in user_data.items():
        if not isinstance(entry, dict):
            continue
        stale_files = entry.get("stale_critical_files")
        if not stale_files:
            continue
        file_descs = ", ".join(
            f"{f['file']} ({f['age_hours']}h)" for f in stale_files
        )
        alerts.append(f"{uid}: {file_descs}")
    return alerts


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    for line in format_stale_critical_alerts(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
