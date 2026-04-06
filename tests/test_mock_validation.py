"""Mock validation: every mock coaching message in the test suite must pass the gate.

If a test uses a mock coaching message that contains system internals
(e.g. "SELECT * FROM wearable_daily"), the outbound gate would block it
in production. This test catches that: no mock message should contain
patterns that the gate would flag.

This prevents test pollution where tests accidentally validate messages
that would never survive the production pipeline.
"""

import pytest

from engine.gateway.outbound_gate import validate_outbound


# Every mock coaching message used in scheduler/integration tests.
# Pulled from test_scheduler.py, test_presend_validation.py,
# test_coaching_outcome.py, test_e2e_happy_paths.py,
# test_wearable_connect_link.py, test_zero_data_gate.py.
MOCK_COACHING_MESSAGES = [
    # test_scheduler.py
    "Test morning brief message",
    "Weekly review: sleep averaged 6.8 hours, HRV trending up at 62.",
    "Sleep was 7.2 hours last night. HRV at 58, solid recovery.",
    "Sleep was 6.5 hours. Recovery is lagging, prioritize an early bedtime tonight.",
    "New unique message with enough length to pass the outbound gate check.",
    "Good morning! Here's your brief.",
    "Morning brief: RHR 48, HRV 66, sleep 7.1 hours. Solid night.",
    "Evening wind-down: sleep stack day 30, all 9 habits checked. Bed by 10.",
    "Sleep was 7.1 hours last night. HRV trending up at 62.",
    # test_presend_validation.py
    "Your VO2 max dropped to 32. Concerning decline.",
    "Great sleep last night, 7.5 hours.",
    "Your VO2 max is 47. Looking strong.",
    # test_coaching_outcome.py
    "Your HRV dropped to 52. Try bed by 10:30.",
    "Great job staying consistent!",
    # test_e2e_happy_paths.py
    "Sleep was 7.2 hours last night. HRV at 58, solid recovery.",
    # test_wearable_connect_link.py
    "Good morning Mike. No data available yet.",
    "Good morning Mike. HRV is 52, sleep was 7.1 hours.",
]


class TestMockMessageValidation:
    """Every mock coaching message in the test suite must pass the outbound gate."""

    @pytest.mark.parametrize(
        "message", MOCK_COACHING_MESSAGES,
        ids=[f"mock_{i}" for i in range(len(MOCK_COACHING_MESSAGES))],
    )
    def test_mock_message_passes_gate(self, message):
        result = validate_outbound(message)
        assert result.ok, (
            f"MOCK POLLUTION: Test uses a mock message that the gate would block.\n"
            f"Fix the mock message, not the gate.\n"
            f"Flags: {result.flags}\n"
            f"Details: {result.details}\n"
            f"Message: {message}"
        )

    def test_known_bad_messages_fail_gate(self):
        """Sanity check: messages that SHOULD be blocked ARE blocked."""
        bad_messages = [
            "On it.",  # too short, process narration
            "System health check at 1:54 AM. Issues: mike stale 88h.",  # system diagnostic
            '{"user_id": "andrew", "status": "ok"}',  # JSON blob
        ]
        for msg in bad_messages:
            result = validate_outbound(msg)
            assert not result.ok, f"Gate should have blocked: {msg}"
