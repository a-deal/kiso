"""Tests for evening check-in prompt conditionalization.

Users without an active workout program should not receive "no active
program" messages. The evening prompt must only mention program status
when the user actually has a program.
"""

from unittest.mock import MagicMock, patch

import pytest

from engine.gateway.scheduler import _compose_message


class TestEveningPromptProgramConditional:
    """Evening check-in prompt conditionalizes program status."""

    def _call_compose_and_capture_prompt(self, **kwargs):
        """Call _compose_message with a mocked Anthropic client, return the prompt sent."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [
            MagicMock(text="Test message")
        ]

        with patch("anthropic.Anthropic", return_value=mock_client):
            _compose_message(**kwargs)

        call_args = mock_client.messages.create.call_args
        return call_args.kwargs["messages"][0]["content"]

    def test_no_program_omits_program_line(self):
        """When has_program=False, the prompt should not mention 'program status'."""
        prompt = self._call_compose_and_capture_prompt(
            schedule_type="evening_checkin",
            user_name="Paul",
            context_data={"checkin": {"test": True}},
            anchor_habit=None,
            has_program=False,
        )
        assert "program status" not in prompt.lower()
        assert "active program" not in prompt.lower()

    def test_with_program_includes_program_line(self):
        """When has_program=True, the prompt should mention program status."""
        prompt = self._call_compose_and_capture_prompt(
            schedule_type="evening_checkin",
            user_name="Andrew",
            context_data={"checkin": {"test": True}},
            anchor_habit="bed by 10:30",
            has_program=True,
        )
        assert "program" in prompt.lower()

    def test_morning_brief_unaffected(self):
        """Morning brief prompt doesn't mention program regardless."""
        prompt = self._call_compose_and_capture_prompt(
            schedule_type="morning_brief",
            user_name="Paul",
            context_data={"checkin": {"test": True}},
            has_program=False,
        )
        assert "active program" not in prompt.lower()
