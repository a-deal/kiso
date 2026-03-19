# LinkedIn Post #1 (v2): The AI Health Coach That Knows Your Numbers

**Status:** Draft — needs Andrew review
**Platform:** LinkedIn
**Angle:** Agent story, not score tool. What's actually happening, not what the code does.

---

I built an AI health coach that texts me every morning.

Not a notification. Not a dashboard. A coaching read based on my actual sleep data, heart rate, weight trend, and lab history. It knows what I measured, what I haven't, and what to do next.

Last week I let it coach someone else. He sent me 60+ biomarkers, 3 lab draws, workout logs, and his full supplement stack.

The first thing the system flagged wasn't a lab value. It was that he was taking 8 supplements without confirming his sleep foundation was solid.

That's the failure mode of health optimization culture. Skip the boring stuff, jump to the shiny stuff. The system caught it because it checks foundations first: sleep, movement, nutrition, recovery. If those aren't locked in, nothing else matters yet.

This is what I've been building. An intelligence layer that connects your health data, scores it against clinical guidelines and population data, and coaches forward with structured recommendations. One thing at a time. At your pace. Based on your actual numbers.

It runs on a Mac Mini in my apartment. Your data never leaves your machine. The scoring engine is open source: 20 metrics, NHANES percentiles, 121 tests, MIT license. The coaching layer connects to it via MCP and delivers through WhatsApp.

Today I texted my dad from it. He doesn't know what MCP is. He doesn't need to. He just got a message from a coach who's about to learn his health picture and help him build on it.

The scoring engine is on GitHub. The coaching part is what makes it real.

Link in comments.

---

## Posting Notes

- No URLs in post body
- GitHub link as first comment: github.com/a-deal/health-engine
- The supplements-before-foundations story is the hook
- Dad onboarding is the emotional beat — shows this isn't just for quantified-self nerds
- No @-mention of Paul in this version (story stands alone)
- "Mac Mini in my apartment" grounds it — not a startup pitch, a builder story
- "He doesn't know what MCP is. He doesn't need to." — the punchline that shows this is real
- Soft CTA: link in comments, no "DM me"
