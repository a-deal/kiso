# Show HN / r/QuantifiedSelf: Open-Source Health Scoring Engine

**Status:** Draft
**Platform:** Hacker News (Show HN) or Reddit r/QuantifiedSelf
**Angle:** Technical, open-source, "build your own health agent"

---

## Show HN version

**Title:** Show HN: Health Engine — Open-source health scoring with 20 NHANES-benchmarked metrics

I built an open-source health scoring engine that evaluates 20 health dimensions against CDC population data (NHANES, n=300K+) and clinical guidelines from AHA, ADA, and ESC.

It runs as an MCP server with Claude. You talk to it naturally: "How am I doing?" gets a coaching read. "I weighed 192 this morning" logs and trends it. "What should I measure next?" ranks gaps by impact and cost.

What it scores: blood pressure, ApoB, fasting insulin/glucose, sleep consistency, Lp(a), RHR, steps, VO2 max, HRV, hs-CRP, liver panel, CBC, TSH, iron, vitamin D, waist circumference, zone 2 minutes, weight trends, family history, medications, PHQ-9.

How scoring works:
- Clinical zones (Optimal/Healthy/Borderline/Elevated) from published guidelines
- Population percentiles from NHANES 2017-2020
- Data freshness decay (18-month-old labs get partial credit)
- Reliability weighting (single BP reading vs 7-day average)
- Compound pattern detection (metabolic syndrome, insulin resistance, atherogenic dyslipidemia)

Integrations: Garmin Connect API (auto-pull HR, HRV, sleep, steps, VO2 max, zone 2) and Apple Health XML import.

Going from 0% to full coverage costs under $300. A wearable alone closes 6 of 20 gaps for free.

Everything runs locally. No cloud, no accounts, no data leaves your machine.

121 tests. Python 3.11+. MIT license.

GitHub: github.com/a-deal/health-engine

---

## r/QuantifiedSelf version

**Title:** I ranked 20 health metrics by evidence strength and built an open-source engine to score them

I've been tracking my health data for 2+ years across 7 blood draws, a Garmin watch, and a home BP cuff. The problem: nothing connects it. Labs in one portal, wearable data in another app, weight on a scale. No tool tells you what it all means together.

So I built one. Health Engine scores 20 dimensions against CDC population data (NHANES, 300K+ Americans) and clinical guidelines. Each metric gets a clinical zone (Optimal through Elevated) and a population percentile. It detects compound patterns like metabolic syndrome and insulin resistance automatically.

The part that surprised me: with 7 blood draws and 200+ biomarkers, my health picture was still only 42% complete. The gaps weren't exotic tests. They were things like Lp(a) ($30, once in your life, 20% chance it changes your risk profile), fasting insulin (catches insulin resistance 10-15 years before glucose moves), and a simple waist measurement.

It works with Claude as an MCP server. Ask "what should I measure next?" and it ranks gaps by leverage and cost. Connect your Garmin and it closes 6 gaps at once.

Everything local-first. Open source. 121 tests.

github.com/a-deal/health-engine

---

## Posting Notes

- Show HN: lead with technical details, scoring methodology, what makes it different
- r/QuantifiedSelf: lead with personal story, coverage gap surprise, actionable insight
- Both end with GitHub link (OK for these platforms)
- Anticipate questions: "why not just use ChatGPT with labs?" — no continuity, no scoring, no compound patterns, no wearable integration
- Anticipate: "is this medical advice?" — no, population percentiles and published guidelines, not diagnoses
- Anticipate: "why MCP/Claude?" — because natural language is a better interface for health data than dashboards
