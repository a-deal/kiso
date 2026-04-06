"""Tests for outbound gate against REAL production messages.

These messages were sent to Andrew on April 5, 2026.
Every one of them is a bug. The outbound gate should flag all of them.

This is the evidence layer: if a message like these gets through in the
future, these tests catch it before a user does.
"""

import pytest

from engine.gateway.outbound_gate import validate_outbound


class TestSystemHealthCheckMessages:
    """System health checks should NEVER reach users. 5 leaked on April 5."""

    def test_health_check_154am(self):
        msg = (
            "System health check at 1:54 AM. Issues:\n"
            "mike — user data stale 88h (threshold 72h)\n"
            "mike — briefing stale 350h, needs regeneration\n"
            "paul — Apple Health sync stale 64h (threshold 48h)\n"
            "Action: Pull fresh Garmin for mike + paul. Regenerate briefings."
        )
        result = validate_outbound(msg)
        assert not result.ok, f"System health check should be flagged. Got: {result}"

    def test_health_check_554am(self):
        msg = (
            "System health check — 3 problems:\n"
            "mike: user_data stale 92h (72h threshold)\n"
            "paul: apple_health stale 68h (48h threshold)\n"
            "Auto-remediation cleared errored crons, API healthy, no zombies. "
            "These data syncs need manual attention."
        )
        result = validate_outbound(msg)
        assert not result.ok, f"System health check should be flagged. Got: {result}"

    def test_health_check_954am(self):
        msg = (
            "System health check: 2 problems detected.\n"
            "mike: user_data stale 96h (threshold 72h).\n"
            "Your wearable sources changed. Auto-remediation ran."
        )
        result = validate_outbound(msg)
        assert not result.ok, f"System health check should be flagged. Got: {result}"

    def test_health_check_154pm(self):
        msg = (
            "System health check flagged 2 items:\n"
            "Mike's data is stale (100h old). Last checkin needed.\n"
            "Your wearable sources changed: RHR, HRV, sleep shifted from "
            "Garmin to Apple Health. Monitor for scoring continuity."
        )
        result = validate_outbound(msg)
        assert not result.ok, f"System health check should be flagged. Got: {result}"

    def test_health_check_554pm(self):
        msg = (
            "System health check alert — 04/05:\n"
            "mike: user_data stale 104.1h (threshold 72h). Briefing stale 366.2h. Stuck 7 days.\n"
            "dad: stuck 7 days with no check-ins.\n"
            "andrew: wearable source changed.\n"
            "Action needed: Check mike's connection and re-engagement status."
        )
        result = validate_outbound(msg)
        assert not result.ok, f"System health check should be flagged. Got: {result}"


class TestAgentProcessNarration:
    """Agent thinking-out-loud messages should not reach users."""

    def test_on_it(self):
        result = validate_outbound("On it.")
        assert not result.ok, "Short process narration should be flagged"

    def test_logged(self):
        result = validate_outbound("Logged.")
        assert not result.ok, "Short process narration should be flagged"

    def test_logging_in_parallel(self):
        result = validate_outbound("Now logging the rest in parallel.")
        assert not result.ok, "Process narration should be flagged"

    def test_all_logged_reading_from_disk(self):
        result = validate_outbound("All logged. Reading from disk now.")
        assert not result.ok, "Process narration should be flagged"

    def test_all_on_disk(self):
        result = validate_outbound("All on disk.")
        assert not result.ok, "Short process narration should be flagged"

    def test_meta_delivery_confirmation(self):
        msg = (
            "Evening wind-down delivered to Andrew (WhatsApp).\n"
            "Protocols check: Sleep Stack is Day 30/Phase 3 (Sustaining gains). "
            "Sleep duration still below target at 6.1hr (target 7.5)."
        )
        result = validate_outbound(msg)
        assert not result.ok, "Meta delivery confirmation should be flagged"

    def test_human_judgment_needed(self):
        msg = (
            "Human judgment needed: Grigoriy's HRV drop is likely post-activity "
            "recovery, not concerning if he sleeps early tonight. Manny's silence "
            "suggests friction on wearable setup."
        )
        result = validate_outbound(msg)
        assert not result.ok, "Internal triage should be flagged"


class TestGoodCoachingStillPasses:
    """Verify we don't break actual coaching messages while fixing leaks."""

    def test_sleep_coaching(self):
        msg = (
            "Sleep stack Day 30. You hit all 9 habits last night. "
            "Regularity is tightening (36 min stdev, down from where we started). "
            "Duration still sitting at 6.1hr, target 7.5. Tonight's the move: "
            "7 PM meal cutoff hard stop, AC 67, hot shower, earplugs, bed by 10."
        )
        result = validate_outbound(msg)
        assert result.ok, f"Good coaching should pass. Flags: {result.details}"

    def test_weight_logging_response(self):
        msg = (
            "Weight: 189.7 lbs. Sleep: 9:30 PM to 6:00 AM (8.5 hrs). "
            "Hot shower, AC 67, earplugs, AM supplements, creatine: done. "
            "8.5 hours is a big night. That's going to show in your recovery numbers."
        )
        result = validate_outbound(msg)
        assert result.ok, f"Weight logging response should pass. Flags: {result.details}"

    def test_meal_log_summary(self):
        msg = (
            "From the log:\n"
            "Premier Protein shake (morning) — 30g / 160 cal\n"
            "RX Bar blueberry x2 — 24g / 360 cal\n"
            "Subway footlong double chicken — 76g / 1,000 cal\n"
            "Total: 192g protein, 2,420 cal."
        )
        result = validate_outbound(msg)
        assert result.ok, f"Meal log should pass. Flags: {result.details}"

    def test_weekly_snapshot(self):
        msg = (
            "ANDREW — Week 7 of Cut (Feb 4 — May 16)\n"
            "Weight: 190.0 lbs (7d avg 190.1). Trend -0.6 lbs/wk.\n"
            "Sleep: 6.1 hrs avg (target 7.5). Debt: 7.5 hours cumulative.\n"
            "Recovery: HRV 66ms (90th percentile, solid). RHR 48.3 bpm."
        )
        result = validate_outbound(msg)
        assert result.ok, f"Weekly snapshot should pass. Flags: {result.details}"

    def test_exercise_advice(self):
        msg = (
            "Seated single-arm DB overhead press. 4x8-10 @ RPE 8. "
            "Primary anterior/medial delt driver. Load it. "
            "Cable lateral raise (low pulley, arm across body). 4x15-20, "
            "3-4 sec eccentric. This is the medial delt builder."
        )
        result = validate_outbound(msg)
        assert result.ok, f"Exercise advice should pass. Flags: {result.details}"
