"""Tests for the phone-number mute list in twilio_sms._forward_to_openclaw.

Added 2026-04-13 as part of the full Paul-stop (architecture audit session).
Context: nulling Paul's person.channel column blocked the scheduler and the
admin manual-send endpoint, but the inbound-reply flow bypasses person entirely
(it replies to the originating phone via `openclaw agent --to <from_number>`).
The mute list is the only place that blocks that path.
"""

from unittest.mock import patch

from engine.gateway import twilio_sms


def test_muted_phone_does_not_spawn_openclaw():
    """A from_number in _MUTED_PHONES must not invoke the openclaw subprocess."""
    muted = next(iter(twilio_sms._MUTED_PHONES))
    with patch.object(twilio_sms.subprocess, "run") as mock_run:
        twilio_sms._forward_to_openclaw(muted, "hi milo", "paul")
    assert mock_run.call_count == 0, (
        "muted phone should short-circuit before subprocess.run is called"
    )


def test_non_muted_phone_still_spawns_openclaw():
    """A non-muted from_number must still invoke openclaw (regression guard)."""
    with patch.object(twilio_sms.subprocess, "run") as mock_run:
        twilio_sms._forward_to_openclaw("+14152009584", "hi milo", "andrew")
    assert mock_run.call_count == 1, (
        "non-muted phone should still reach subprocess.run"
    )


def test_muted_phone_does_not_hit_twilio_api():
    """send_sms must short-circuit for a muted phone without calling the REST API."""
    muted = next(iter(twilio_sms._MUTED_PHONES))
    with patch.object(twilio_sms.requests, "post") as mock_post:
        result = twilio_sms.send_sms(
            to=muted,
            body="hi",
            user_id="paul",
            account_sid="ACfake",
            auth_token="faketoken",
            from_number="+15550000000",
        )
    assert mock_post.call_count == 0, (
        "muted phone should short-circuit before requests.post is called"
    )
    assert result["status"] == "muted"
    assert result["to"] == muted


def test_send_sms_non_muted_phone_still_hits_api():
    """Regression guard: send_sms must still reach requests.post for non-muted phones."""
    fake_response = type("R", (), {
        "status_code": 201,
        "json": lambda self: {"sid": "SMfake"},
    })()
    with patch.object(twilio_sms.requests, "post", return_value=fake_response) as mock_post:
        result = twilio_sms.send_sms(
            to="+14152009584",
            body="hi",
            user_id="andrew",
            account_sid="ACfake",
            auth_token="faketoken",
            from_number="+15550000000",
        )
    assert mock_post.call_count == 1
    assert result["status"] == "ok"


def test_paul_phone_is_muted():
    """Explicit assertion that Paul's phone is in the mute list.

    This is a tripwire: if the mute list is cleared without updating the
    audit context, this test fails loudly so someone thinks about it.
    """
    assert "+17038878948" in twilio_sms._MUTED_PHONES
