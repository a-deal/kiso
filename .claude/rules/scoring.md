---
paths:
  - "engine/scoring/**"
  - "engine/scoring/*.py"
  - "engine/scoring/*.yaml"
---

# Scoring Engine Rules

- Never change clinical thresholds (alert thresholds, significance thresholds, retest cadence) without citing a source from the research docs at `hub/research/`.
- Rolling average window sizes (7d, 30d) are evidence-based. Don't change them without reading `hub/research/2026-03-27-health-metrics-timescale-framework.md`.
- The condition_modifiers.yaml contains medical coaching context. Changes require review. Don't add conditions without researching their interaction with each alert type.
- All metrics must work for any user, not just Andrew. No hardcoded user-specific values.
