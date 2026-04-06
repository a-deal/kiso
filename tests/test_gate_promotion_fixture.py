"""Gate promotion fixture: real production messages must pass outbound gate.

The outbound gate can't promote from audit mode to enforcement mode until
it has 0% false positive rate on real production coaching messages. This
test is the fixture that proves it.

Every message here was actually sent to a real user via the scheduler.
If the gate flags any of them, it's a false positive that blocks promotion.
"""

import pytest

from engine.gateway.outbound_gate import validate_outbound


# Real production messages pulled from conversation_message table April 6, 2026.
# All sender_name='milo-scheduler', role='assistant'.
REAL_PRODUCTION_MESSAGES = [
    # Grigoriy morning brief — HRV recovery coaching
    (
        "Your HRV this morning is 18.3ms against your 7-day average of 35.7ms, "
        "that's a significant dip and your body is signaling it needs recovery today. "
        "Your resting heart rate ticked up to 62 bpm versus your 61 bpm average, "
        "which lines up with the same story. You logged 21,329 steps yesterday, "
        "nearly double your norm, so the suppressed HRV makes sense. Keep today's "
        "movement easy and deliberate: a walk is fine, but hold off on anything "
        "intense until your numbers bounce back.\n\n"
        "Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html\n\n"
        "Reply 'connect' and I'll help you link your wearable."
    ),
    # Paul evening check-in — anchor habit + weight
    (
        "No active program running yet, so tonight is about building the foundation. "
        "You haven't set an anchor habit, and that's actually the right place to start: "
        "one small daily action that locks in consistency before we layer anything else "
        "on top. Given your bedtime variance sitting at plus or minus 66 minutes, I'd "
        "suggest your anchor habit be a fixed wake time, same time every day including "
        "weekends, which is the fastest lever for tightening that circadian rhythm. "
        "Log your weight tomorrow morning before eating and we'll have four data points "
        "to start seeing a real trend off that 155 lb reading from yesterday."
    ),
    # Andrew evening check-in — sleep stack day 30
    (
        "You're on day 30 of your Sleep Stack and the outcomes aren't there yet: "
        "6.1 hours average against a 7.5-hour target, and bedtime variability still "
        "at 36 minutes when we want it under 30. You haven't set an anchor habit yet, "
        "and that's actually the missing piece here. Let's pick one small thing to lock "
        "in first: a fixed bedtime alarm at 10pm every night, no exceptions. Nutrition "
        "is covered for today, you're actually 120 calories over on a rest day so close "
        "the kitchen now. Tonight: magnesium before bed, earplugs in, and no caffeine "
        "if you haven't cut it off already.\n\n"
        "[Note: some metrics may reflect a wearable source change, not an actual change "
        "in your health. sleep_hrs source changed.]"
    ),
    # Grigoriy evening check-in — step count celebration + HRV warning
    (
        "You crushed your step count today, 21,329 steps against your 7-day average "
        "of 10,724, that's a strong output day. You haven't set an anchor habit yet, "
        "and that's actually a great place to start: how about a simple \"kitchen closed "
        "by 9pm\" rule, something small enough to do every day that compounds over weeks. "
        "No meals are logged today, so if you haven't tracked dinner yet, get that in "
        "before you wind down. Your HRV dropped to 18.3ms today versus your 35.7ms "
        "weekly average, so after a big movement day like this, prioritize an early "
        "bedtime tonight.\n\n"
        "Connect your Garmin here: https://auth.mybaseline.health/auth/garmin"
        "?user=grigoriy&state=grigoriy:garmin:493169:84ae2aa818a94878"
    ),
    # Grigoriy morning brief — movement focus
    (
        "No sleep data came through from Apple Health last night, so I can't give you "
        "numbers there. Your top signal to watch is daily movement: 120 steps per day "
        "is the lowest reading in your profile and sits at the 10th percentile, which "
        "is the clearest lever you can pull right now given your VO2 max of 32.3 and "
        "borderline LDL at 135. Your RHR at 61 and HRV at 44.9 are both solid, so "
        "your body is ready. Today's one focus: get outside and accumulate at least "
        "7,000 steps, even broken into short walks throughout the day.\n\n"
        "Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html\n\n"
        "Connect your Garmin here: https://auth.mybaseline.health/auth/garmin"
        "?user=grigoriy&state=grigoriy:garmin:493156:95da722af77c3378"
    ),
    # Grigoriy evening — kitchen habit + recovery
    (
        "No active program running yet, so tonight is a good time to plant one small "
        "root: you have no anchor habit set, and I'd suggest starting with a simple "
        "\"kitchen closed by 9pm\" rule since you already have that habit tracked and "
        "it costs zero effort to define. No meals were logged today, so if you ate, "
        "take 60 seconds to drop them in before bed. Your HRV is sitting at a solid "
        "44.9ms and RHR at 61bpm, both steady, so recovery looks fine heading into "
        "tomorrow.\n\n"
        "Connect your Garmin here: https://auth.mybaseline.health/auth/garmin"
        "?user=grigoriy&state=grigoriy:garmin:493145:9a85a08b1ab5d8ee"
    ),
    # Grigoriy morning — VO2 max coaching
    (
        "Sleep data isn't available from Apple Health this morning, so that piece of "
        "the picture is missing today. Your top signal to watch is your VO2 max at "
        "32.3 mL/kg/min, which puts you in the bottom quarter for your age and is the "
        "strongest modifiable predictor of long-term mortality on your board. Your steps "
        "today are sitting at 120, and that pattern is the most direct lever you have to "
        "start moving that number. One focus today: get outside for a 20 to 30 minute "
        "walk, no agenda, just accumulated movement.\n\n"
        "Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html"
    ),
    # Zero-data user — calibration mode
    (
        "No active program yet. We're still in calibration mode and need about 27 more "
        "days of data before your training load becomes meaningful. Did you complete your "
        "anchor habit today? Also, no meals are logged for today, so if you ate, get those "
        "in before bed. No specific tonight protocol is set, but connecting a wearable would "
        "be the single biggest move you can make right now, unlocking 5 key metrics "
        "automatically."
    ),
    # Zero-data user — first engagement
    (
        "No active program is set up yet, and right now there's no data coming in from "
        "meals, habits, or wearables, so tonight is a good moment to start building the "
        "baseline. Did you get your anchor habit in today, whatever that looks like for "
        "you right now? There's nothing logged for meals today, so if you can recall "
        "breakfast, lunch, or dinner, even a rough note helps. No specific tonight "
        "protocol is on file yet, but the single highest-leverage move before bed would "
        "be getting a blood pressure reading if you have a cuff, or putting an Omron on "
        "your radar as a first step."
    ),
    # Grigoriy morning — simple movement nudge
    (
        "No sleep data came through from Apple Health last night, so no duration or timing "
        "to report. Your top signal to watch is activity: 120 steps per day is the lowest "
        "reading in your profile, sitting at the 10th percentile, and that number needs to "
        "move. Today's one focus is simple: get outside or find any reason to walk, even 20 "
        "minutes adds roughly 2,000 steps and starts bending that trend in the right "
        "direction.\n\n"
        "Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html"
    ),
]


class TestGatePromotionFixture:
    """Every real production coaching message must pass the outbound gate.

    If ANY message here fails, the gate has a false positive and cannot
    promote from audit mode to enforcement mode.
    """

    @pytest.mark.parametrize(
        "message", REAL_PRODUCTION_MESSAGES,
        ids=[f"msg_{i}" for i in range(len(REAL_PRODUCTION_MESSAGES))],
    )
    def test_real_message_passes_gate(self, message):
        result = validate_outbound(message)
        assert result.ok, (
            f"FALSE POSITIVE: Gate blocked a real coaching message.\n"
            f"Flags: {result.flags}\n"
            f"Details: {result.details}\n"
            f"Message preview: {message[:200]}..."
        )

    def test_fixture_has_minimum_coverage(self):
        """Fixture must have at least 10 real messages to be meaningful."""
        assert len(REAL_PRODUCTION_MESSAGES) >= 10
