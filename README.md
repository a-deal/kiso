# health-engine

Open-source health intelligence engine. Scores your body composition, recovery, and biomarkers against population data. Tells you where you stand, what's missing, and what to do next.

## What It Does Today

**Body recomposition + recovery tracking.** This is built for people actively managing their body — cutting weight, building strength, dialing in nutrition — who want data-driven feedback on whether it's working and whether the cost is sustainable.

Specifically:
- **Scoring** — 20 health metrics scored against NHANES population percentiles (real CDC survey data, not arbitrary ranges). You get a percentile and a standing for every metric you feed it.
- **Lab import** — feed it your blood work (lipids, metabolic panel, CBC, thyroid, inflammatory markers, Lp(a)) and it slots every value into population context. 18 lab markers supported today.
- **Insights** — rule-based coaching signals from wearable data: HRV dropping? Sleep debt accumulating? Deficit too aggressive for your recovery? It flags compound effects, not just thresholds.
- **Garmin integration** — pulls RHR, HRV, sleep, steps, VO2 max, zone 2 minutes, workouts, and daily calorie burn from Garmin Connect.
- **Tracking** — weight trends with rolling averages, remaining-to-hit macros, 1RM estimation (RPE-based), DOTS score, habit streak analysis.

All local. Zero PII in the repo. Your data stays on your machine.

## The Coverage Journey

The engine scores 20 metrics. Your **coverage score** tells you how many you've filled in — and the **gap analysis** tells you exactly what's missing and what it costs to close each one.

You don't need everything on day one. Start with what you have, and the engine tells you where to invest next.

### What gets you to 100%

| Coverage | What You Need | Cost |
|---|---|---|
| **~20%** | Garmin watch (or any wearable) | Already own one? You're here. |
| **~35%** | + bathroom scale + blood pressure cuff | ~$60 total |
| **~75%** | + one blood draw (lipid panel, metabolic, CBC, thyroid, hs-CRP) | ~$100-200 via Quest/walk-in lab |
| **~90%** | + waist circumference (tape measure) + family history + medication list | Free |
| **100%** | + PHQ-9 mental health screen | Free (3-min questionnaire) |

### Suggested Gear

You probably own most of this already. If not, here's what closes the gaps:

| Metric | Gear | Notes |
|---|---|---|
| RHR, HRV, Sleep, Steps, Zone 2 | **Garmin watch** (Venu, Forerunner, Fenix) | Any model with optical HR sensor. Also works with Oura, Apple Watch, WHOOP. |
| Weight Trends | **Digital scale** | Any accurate scale. Weigh daily, the engine computes rolling averages. |
| Blood Pressure | **Omron home BP cuff** | ~$40. Take 3 readings, log the average. Far more useful than a single office reading. |
| Waist Circumference | **Tape measure** | $3. Measure at the navel, first thing in the morning. |
| Labs (Lipids, Metabolic, CBC, Thyroid, hs-CRP, Lp(a)) | **Blood draw** | Quest Diagnostics walk-in or order through your doctor. A basic panel runs $30-100. Add ApoB and fasting insulin for the full picture. |
| VO2 Max | **Your wearable** | Most GPS watches estimate this from running data. No extra gear needed. |

The gap analysis in `python3 cli.py score` ranks these by information value — which $30 test or $40 device gives you the biggest jump in coverage.

## Where It's Going

The scoring engine and insight rules are the foundation. The interesting directions:

**Longitudinal intelligence.** Right now it's snapshot-based — "here's where you stand today." The next layer is time-series: how are your markers *trending* over months? Your HRV is 62ms today — is that up from 50 or down from 75? The trend changes the insight completely. Daily series pull is already built; the analysis layer is next.

**Lab import + full health picture.** Lab import is live today — the engine scores lipids, metabolic panel, CBC, thyroid, hs-CRP, liver enzymes, Lp(a), ferritin, and vitamin D against NHANES population data. Drop your values into `lab_results.json` and they flow into your coverage score and coaching briefing. The next step is automated PDF parsing — feed it a Quest or Labcorp PDF and it extracts everything automatically.

**Protocol engine.** Once you have scores + trends, the next question is *what do I do about it?* Sleep regularity bad? Here's a 2-week circadian protocol. HRV declining? Here's a recovery week template. This is where the insight rules evolve into actionable plans — the bridge between "what's happening" and "what to change."

**Multi-source fusion.** Garmin is first, but the architecture supports any wearable (Oura, Apple Health, Whoop, Fitbit). Different devices, same health model. The JS ports mean this can run client-side in a browser or be consumed by a native iOS/Android app.

**AI coaching layer.** The rules engine generates structured insights (severity, category, body text). Feed those to an LLM and you get a conversational health coach that's grounded in your actual data — not generic advice. The insight objects are designed for this: structured enough for code, readable enough for a model. Open the project with Claude Code and say "how am I doing?" — the `CLAUDE.md` playbook teaches it to pull your briefing, assess where you stand, and coach you forward. No scripts to memorize, just a conversation.

**Voice-first check-ins.** Pair with a speech-to-text layer (Whisper, Superwhisper, or system dictation) and your morning check-in becomes a conversation — talk to your health coach, get a read on your numbers, hear what to focus on today. The coaching voice is written to sound natural spoken aloud.

## Example: Morning Check-in

Open the project with Claude Code and say *"how am I doing?"* Here's what a real check-in looks like — Claude pulls the briefing behind the scenes and coaches from your actual numbers:

> **Morning read — March 10**
>
> Recovery looks solid. RHR at 50.5 and trending down hard (57 → 50 over 90 days), HRV at 64 and trending up. Your body is adapting well to the cut — cardiovascular fitness is improving even in a deficit. Zone 2 at 164 min/week is doing its job.
>
> The weight picture is honest: 194.6 this morning, 6.6 lbs from target. The weekly rate calculated to 0.1 lbs — basically flat over the last week. That could be a stall, water retention, or just noise. Your 7-day rolling average is 194.6 which is down from where you were, so the trend is still headed the right direction. Worth watching this week to see if it breaks.
>
> Sleep is still your weakest link. 6.5 hrs average, ±107 min bedtime variance. That regularity number is roughly 10th percentile — it's the one metric dragging your whole picture down. You built a sleep habits checklist but the data shows almost none of them are sticking yet. The AM sunlight streak of 3 days is the only one with momentum.
>
> You've had 54g protein so far today. 136g to go. You need a big protein-forward meal this afternoon and evening.
>
> **Two things for today:**
> 1. Log tonight's habits honestly. Pick two from your sleep list and do them.
> 2. Hit 150g protein minimum. A double steak bowl gets you 55g in one shot.

No scripts to run. No dashboard to check. You talk, it reads your data, it coaches. The `CLAUDE.md` playbook teaches any LLM to do this — pull the structured briefing, interpret the scores and trends, and give forward guidance grounded in real numbers.

Under the hood, `python3 cli.py briefing` produces a single JSON snapshot with everything: scores, insights, weight trend, nutrition remaining, strength progression, habit streaks, and compound coaching signals. The LLM never invents data — it reads what the engine computed.

## Get Started (2 minutes)

### Option A: Interactive setup

```bash
git clone https://github.com/a-deal/health-engine.git
cd health-engine
./setup.sh
```

Walks you through everything: dependencies, config, Garmin connection, verification.

### Option B: Use with Claude Code

```bash
git clone https://github.com/a-deal/health-engine.git
cd health-engine
claude
```

Tell Claude: *"Help me get set up."* The `CLAUDE.md` file gives it full project context — it'll create your config, explain the scoring, and help you interpret results.

Works with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or Claude Desktop.

### Option C: Manual

```bash
git clone https://github.com/a-deal/health-engine.git
cd health-engine
python3 -m pip install -e .          # core
python3 -m pip install -e ".[garmin]" # + Garmin integration
cp config.example.yaml config.yaml   # edit with your age, sex, targets
python3 cli.py score                 # see your gaps
```

## CLI

```bash
python3 cli.py score                            # Score profile (shows gaps)
python3 cli.py score --profile data/me.json     # Score from a profile JSON
python3 cli.py pull garmin                      # Pull Garmin Connect data
python3 cli.py pull garmin --history --workouts # + 90-day trends + workout sets
python3 cli.py insights                         # Generate health insights
python3 cli.py status                           # Check what data files exist
```

## What's Inside

```
engine/
├── scoring/        # 20 metrics × NHANES percentiles → coverage + assessment + gaps
├── insights/       # Rule-based coaching (HRV, RHR, sleep, weight, BP) + configurable thresholds
├── integrations/   # Garmin Connect API (RHR, HRV, sleep, steps, VO2, workouts, burn)
├── tracking/       # Weight trends, macros (remaining-to-hit), 1RM/DOTS, habit streaks
└── data/           # NHANES percentile tables (ships with package)

js/                 # Client-side JavaScript ports of scoring + insights
```

## Use as a Library

```python
from engine.models import Demographics, UserProfile
from engine.scoring.engine import score_profile
from engine.insights.engine import generate_insights

profile = UserProfile(
    demographics=Demographics(age=35, sex="M"),
    resting_hr=52, hrv_rmssd_avg=62, vo2_max=47,
)
output = score_profile(profile)
print(f"Coverage: {output['coverage_score']}%")

insights = generate_insights(garmin={"resting_hr": 52, "hrv_rmssd_avg": 62})
for i in insights:
    print(f"[{i.severity}] {i.title}")
```

## Configuration

All personal data stays in `config.yaml` (gitignored). Insight thresholds are configurable in `engine/insights/rules.yaml`. See [DATA_FORMATS.md](docs/DATA_FORMATS.md) for CSV/JSON schemas.

## Docs

- [ONBOARDING.md](docs/ONBOARDING.md) — Full setup walkthrough
- [SCORING.md](docs/SCORING.md) — How the scoring engine works
- [METRICS.md](docs/METRICS.md) — 20-metric catalog with evidence and sources
- [DATA_FORMATS.md](docs/DATA_FORMATS.md) — CSV/JSON schemas

## Tests

```bash
python3 -m pytest tests/ -v   # 24 tests, <0.1s
```

## License

MIT
