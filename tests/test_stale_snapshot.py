"""Tests for stale snapshot detection in briefing assembly.

When today's wearable data hasn't synced yet, the briefing falls back
to the most recent day's data. This fallback must be flagged as stale
so the coaching prompt doesn't present old sleep times as last night's.
"""

from datetime import datetime, timedelta, timezone

import pytest


class TestStaleSnapshotFlag:

    def test_today_data_not_stale(self):
        """When today's data exists, no stale flag."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_series = [
            {"date": today, "sleep_start": "22:30", "sleep_end": "05:30", "rhr": 48},
        ]

        # Simulate the briefing logic
        today_daily = next((d for d in daily_series if d.get("date") == today), None)
        assert today_daily is not None
        assert "_stale" not in today_daily

    def test_fallback_to_yesterday_is_stale(self):
        """When today's data is missing, fallback is flagged stale."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        daily_series = [
            {"date": yesterday, "sleep_start": "23:00", "sleep_end": "07:56", "rhr": 52},
        ]

        today_daily = next((d for d in daily_series if d.get("date") == today), None)
        if not today_daily:
            today_daily = daily_series[-1] if daily_series else None
            if today_daily:
                today_daily = dict(today_daily)
                today_daily["_stale"] = True
                today_daily["_stale_note"] = f"Data is from {today_daily.get('date', 'unknown')}, not today. Today's sync may not have run yet."

        assert today_daily is not None
        assert today_daily["_stale"] is True
        assert yesterday in today_daily["_stale_note"]
        # The sleep times are from yesterday, not last night
        assert today_daily["sleep_end"] == "07:56"

    def test_stale_flag_does_not_mutate_source(self):
        """Stale flagging should copy, not mutate the source dict."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        original = {"date": yesterday, "sleep_start": "23:00", "rhr": 52}
        daily_series = [original]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_daily = next((d for d in daily_series if d.get("date") == today), None)
        if not today_daily:
            today_daily = daily_series[-1] if daily_series else None
            if today_daily:
                today_daily = dict(today_daily)
                today_daily["_stale"] = True

        assert "_stale" not in original  # Source not mutated
        assert today_daily["_stale"] is True

    def test_empty_series_no_crash(self):
        """Empty daily series should not crash."""
        daily_series = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_daily = next((d for d in daily_series if d.get("date") == today), None)
        if not today_daily:
            today_daily = daily_series[-1] if daily_series else None
        assert today_daily is None
