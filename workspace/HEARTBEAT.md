# HEARTBEAT — Proactive Monitoring Schedule

## Message Sending Rule

Every `message` tool call MUST include `buttons=[]`. The tool validation requires this field even when no buttons are needed. Omitting it causes a silent validation error and the message will not be sent.

```
message(action="send", channel=user.channel, target=user.channel_target, message=..., buttons=[])
```

## Multi-User Loop

All scheduled checks and briefings loop over every user in `users.yaml`.
Pass `user_id` to every tool call. Never mix data between users.

Each user entry includes:
- `channel`: "whatsapp" or "telegram"
- `channel_target`: phone number for WhatsApp, numeric user ID for Telegram
- `timezone`: IANA timezone string (e.g. "America/Los_Angeles", "Europe/Moscow")

```
for each user in users.yaml:
    user_id = user.user_id
    name = user.name
    channel = user.channel
    target = user.channel_target
    tz = user.timezone
```

## Heartbeat (Every 30 Minutes)

Silent check per user. No message unless action needed.

```
for each user in users.yaml:
    # Check if user is in quiet hours (9:15 PM - 6:00 AM in THEIR timezone)
    if user_local_time(user.timezone) is in quiet hours: skip

    call checkin(user_id=user.user_id)
    read coaching_signals

    if any signal.severity == "critical":
        send message(channel=user.channel, target=user.channel_target, message=..., buttons=[])

    if 2+ signals.severity == "warning":
        send message(channel=user.channel, target=user.channel_target, message=..., buttons=[])

    else:
        log to daily memory, surface at next scheduled check-in
```

### Critical Triggers (Interrupt Immediately)

| Signal | Condition | Message Style |
|---|---|---|
| HRV collapse | HRV <50ms | "HRV dropped to [X]. Skip today's session, prioritize sleep tonight." |
| RHR spike | RHR >55 during deficit | "RHR at [X] — your body's flagging the deficit. Consider a maintenance day." |
| Sleep crisis | Debt >7hr over 7 days | "You're running on fumes — [X]hr debt this week. Tonight is non-negotiable: bed by 10." |
| Deficit unsustainable | Loss >2lb/wk + HRV <55 | "Losing too fast with HRV at [X]. Back off to maintenance calories today." |

### Warning Triggers (Hold for Next Check-in)

| Signal | Condition |
|---|---|
| Sleep debt moderate | >3.5hr over 7 days |
| HRV declining | 50-55ms |
| Sleep-deficit interaction | Sleep <7hr + active cut + HRV <55 |
| Sleep regularity | Bedtime stdev >60min |
| Unplanned surplus | Calories >130% of target |
| Late meal | Meal within 2hr of bedtime |

## Morning Brief — 7:00 AM (user's local time)

```
for each user in users.yaml:
    # Only send if it's ~7:00 AM in the user's timezone
    if user_local_time(user.timezone) is not near 7:00 AM: skip

    # Only pull Garmin if user has it connected
    call connect_garmin(user_id=user.user_id)
    if has_tokens: call pull_garmin(history=true, user_id=user.user_id)

    call checkin(user_id=user.user_id)

    # Skip users with no profile yet (empty briefing)
    if no profile configured: skip

    compose message:
      1. Last night's sleep (duration, quality, bed/wake times)
      2. Top signal from coaching_signals (1-1-1 rule)
      3. Today's one focus

    # Adapt tone: new users get more explanation, experienced users get concise coaching
    # Include dashboard link at the end of every morning brief
    append to message:
      "\nYour dashboard: https://dashboard.mybaseline.health/dashboard/member.html"

    send message(channel=user.channel, target=user.channel_target, message=..., buttons=[])
```

Example: "Slept 6.8hrs, in bed at 10:40. HRV bounced to 68 — recovery is tracking. Today's focus: hit 190g protein, you've been averaging 175 this week.

Your dashboard: https://dashboard.mybaseline.health/dashboard/member.html"

## Evening Wind-Down — 8:00 PM (user's local time)

```
for each user in users.yaml:
    # Only send if it's ~8:00 PM in the user's timezone
    if user_local_time(user.timezone) is not near 8:00 PM: skip

    call get_protocols(user_id=user.user_id)

    compose message:
      1. Active program status (e.g. "Day X of 14 — [habit name]")
      2. Anchor habit check: ask about the ONE tracked habit
      3. Any meals left to log
      4. Protocol reminder for tonight (if applicable)

    send message(channel=user.channel, target=user.channel_target, message=..., buttons=[])
```

Example (Andrew): "Evening routine in 15. You've hit sunlight, no-caffeine, and meal cutoff. Still need: hot shower, AC to 67, earplugs. No meals logged after lunch — did you eat dinner?"

Example (Grigoriy): "Day 2 of 14. Did you close the kitchen after dinner tonight?"

## Weekly Review — Friday 6:00 PM (user's local time)

```
for each user in users.yaml:
    # Only send if it's Friday ~6:00 PM in the user's timezone
    if user_local_time(user.timezone) is not Friday near 6:00 PM: skip

    call score(user_id=user.user_id)
    call checkin(user_id=user.user_id)

    compose message:
      1. Weight trend (this week vs last, pace vs target)
      2. Key metric movements (HRV, RHR, sleep avg)
      3. Protocol compliance (habit percentages)
      4. Coverage gaps (what to measure next)
      5. One thing to focus on next week

    send message(channel=user.channel, target=user.channel_target, message=..., buttons=[])
```

Example: "Week 7 recap. Weight 192.5 → 191.8, pace is 0.7 lb/wk — right in the zone. HRV averaged 68, up from 63 last week. Sleep stack compliance: 78% (bed-only-sleep and evening routine are the misses). Lipid panel is at 42% credit — worth retesting in the next month. Next week: lock in the evening routine. That's the highest-leverage habit you're still inconsistent on."

## Nudge Persistence

Track what's been nudged to avoid repetition:

- Don't send the same nudge twice in 24 hours
- If a warning persists for 3+ days, escalate language once, then back off
- Positive streaks: celebrate at 7, 14, 21 days — not every day
- Data freshness nudges: once per week max per metric

## Quiet Hours

No messages between 9:15 PM and 6:00 AM **in the user's local timezone**. Check `user.timezone` from users.yaml before sending any message.

If a critical signal fires during a user's quiet hours, queue it for their next morning brief with a flag: "Overnight alert: [signal]. Flagging this first thing."
