---
paths:
  - "engine/utils/csv_io.py"
  - "mcp_server/tools.py"
  - "data/**"
---

# Data Integrity Rules

- CSV writes MUST use append_csv for logging operations (log_meal, log_weight, log_bp, log_habits). Never read-all/rewrite for appends.
- write_csv uses atomic rename (.tmp then rename). Don't bypass this.
- Every CSV write must pass validate_row. Required fields are defined in REQUIRED_FIELDS dict in csv_io.py.
- All tool calls that write data MUST include user_id. Omitting user_id writes to the wrong directory.
- Never delete rows from CSVs without explicit user request. Flag and skip bad rows instead.
- After any data migration or fix, run the CSV scan to verify no malformed rows remain.
- See hub/decisions/2026-03-27-csv-data-integrity.md for full root cause analysis.
