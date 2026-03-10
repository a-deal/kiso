# health-engine

Open-source health intelligence engine. Scores your body composition, recovery, and biomarkers against population data. Tells you where you stand, what's missing, and what to do next.

## What It Does Today

**Body recomposition + recovery tracking.** This is built for people actively managing their body — cutting weight, building strength, dialing in nutrition — who want data-driven feedback on whether it's working and whether the cost is sustainable.

Specifically:
- **Scoring** — 20 health metrics scored against NHANES population percentiles (real CDC survey data, not arbitrary ranges). You get a percentile and a standing for every metric you feed it.
- **Insights** — rule-based coaching signals from wearable data: HRV dropping? Sleep debt accumulating? Deficit too aggressive for your recovery? It flags compound effects, not just thresholds.
- **Garmin integration** — pulls RHR, HRV, sleep, steps, VO2 max, zone 2 minutes, workouts, and daily calorie burn from Garmin Connect.
- **Tracking** — weight trends with rolling averages, remaining-to-hit macros, 1RM estimation (RPE-based), DOTS score, habit streak analysis.

All local. Zero PII in the repo. Your data stays on your machine.

## Where It's Going

The scoring engine and insight rules are the foundation. The interesting directions:

**Longitudinal intelligence.** Right now it's snapshot-based — "here's where you stand today." The next layer is time-series: how are your markers *trending* over months? Your HRV is 62ms today — is that up from 50 or down from 75? The trend changes the insight completely. Daily series pull is already built; the analysis layer is next.

**Lab import + full health picture.** The scoring engine handles 20 metrics across blood panels (lipids, metabolic, inflammation, thyroid, CBC), wearable data, and self-report. Feed it a lab PDF and it slots every value into the population context. The gap analysis tells you exactly which $30 blood test would give you the most information.

**Protocol engine.** Once you have scores + trends, the next question is *what do I do about it?* Sleep regularity bad? Here's a 2-week circadian protocol. HRV declining? Here's a recovery week template. This is where the insight rules evolve into actionable plans — the bridge between "what's happening" and "what to change."

**Multi-source fusion.** Garmin is first, but the architecture supports any wearable (Oura, Apple Health, Whoop, Fitbit). Different devices, same health model. The JS ports mean this can run client-side in a browser or be consumed by a native iOS/Android app.

**AI coaching layer.** The rules engine generates structured insights (severity, category, body text). Feed those to an LLM and you get a conversational health coach that's grounded in your actual data — not generic advice. The insight objects are designed for this: structured enough for code, readable enough for a model. Open the project with Claude Code and say "how am I doing?" — the `CLAUDE.md` playbook teaches it to pull your briefing, assess where you stand, and coach you forward. No scripts to memorize, just a conversation.

**Voice-first check-ins.** Pair with a speech-to-text layer (Whisper, Superwhisper, or system dictation) and your morning check-in becomes a conversation — talk to your health coach, get a read on your numbers, hear what to focus on today. The coaching voice is written to sound natural spoken aloud.

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
