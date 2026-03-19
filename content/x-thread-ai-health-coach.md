# X Thread: I Built an AI Health Coach

**Status:** Draft
**Platform:** X/Twitter (thread format)
**Angle:** Builder-in-public + proof via Paul's onboarding

---

**1/**
I built an AI health coach that texts me every morning with my actual numbers.

Not a dashboard. Not a notification. A real coaching read based on my sleep, heart rate, weight trend, and lab history.

Here's what happened when I let it coach someone else.

**2/**
The system tracks 20 health dimensions. Blood pressure. Cholesterol particles. Sleep consistency. VO2 max. Fasting insulin. Each one scored against CDC population data from 300,000+ Americans.

It doesn't just tell you the number. It tells you what it means in context.

**3/**
A friend offered to test it. He sent me 60+ biomarkers, 3 lab draws over 2 years, workout logs, sleep data, supplement list, and diet details.

The agent parsed all of it, built his profile, and started coaching him.

**4/**
First thing it flagged: he was taking 8 supplements but hadn't confirmed his sleep foundation was solid.

That's the failure mode of health optimization culture. Skip the boring stuff, jump to the shiny stuff. The agent caught it because it's programmed to check foundations first.

**5/**
The intervention hierarchy:

- Tier 0: Sleep, movement, nutrition, stress (confirm these first)
- Tier 1: Behavioral changes
- Tier 2: Measurement gaps
- Tier 3: Targeted interventions
- Tier 4: Advanced/experimental

Never recommend Tier N until Tier N-1 is confirmed. Simple rule. Nobody follows it.

**6/**
The scoring engine is open source. 20 metrics, NHANES percentiles, clinical thresholds from AHA/ADA/ESC, compound pattern detection (metabolic syndrome, insulin resistance, recovery stress).

121 tests. MIT license. Works with Claude via MCP.

github.com/a-deal/health-engine

**7/**
What I learned building this:

Your health data exists in 6 different apps that don't talk to each other. The hard problem isn't collecting data. It's connecting it, scoring it honestly, and telling you what to do next.

That's what this does.

**8/**
Next: more users, more protocols, more integrations.

The agent runs on a Mac Mini in my apartment. Local-first. Your data never leaves your machine.

If you build health tools or track your own data, the engine is free to use.

---

## Posting Notes

- No URLs in tweets except thread 6 (GitHub link)
- Keep each tweet under 280 chars where possible
- Real data, real story, no hype
- Paul not named (privacy) — "a friend"
- The supplements-before-foundations story is the hook that lands
- Thread ends with open invitation, not a hard sell
