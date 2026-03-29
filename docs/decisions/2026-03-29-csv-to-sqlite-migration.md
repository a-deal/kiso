# CSV → SQLite Migration: Unified Health Data Layer

**Date:** 2026-03-29
**Status:** In progress
**Goal:** Move all per-user health data from CSVs/JSON files into SQLite (kasane.db). One database, one query interface, proper models.

---

## Current State

### CSVs (per user, at data/users/{user_id}/)

| File | Columns | Purpose | Rows (Andrew) |
|---|---|---|---|
| weight_log.csv | date, weight_lbs, source, waist_in | Body composition tracking | 52 |
| meal_log.csv | date, meal_num, time_of_day, description, protein_g, carbs_g, fat_g, calories, notes | Nutrition logging | 157 |
| bp_log.csv | date, systolic, diastolic, source | Blood pressure tracking | 16 |
| daily_habits.csv | date, + 20 habit columns, notes | Daily habit check-ins | 21 |
| session_log.csv | date, rpe, duration_min, type, name, notes | Training session logging | 1 |
| strength_log.csv | date, exercise, weight_lbs, reps, rpe, notes | Strength training detail | 6 |
| wake_log.csv | date, wake_time, notes | Wake time tracking | 2 |

### JSON files (per user)

| File | Purpose | Migrating? |
|---|---|---|
| garmin_latest.json | Latest Garmin metrics snapshot | Yes → wearable_snapshot table |
| garmin_daily.json | 90-day daily series (RHR, HRV, sleep, steps) | Yes → wearable_daily table |
| garmin_today.json | Today's intraday data | No (ephemeral, rebuilt each pull) |
| garmin_daily_burn.json | Daily calorie burn | Yes → folds into wearable_daily |
| lab_results.json | Lab draws + latest values | Yes → lab_draw + lab_result tables |
| briefing.json | Computed briefing output | No (derived, rebuilt each pull) |

### What's already in SQLite (kasane.db)

person, habit, check_in, check_in_message, focus_plan, health_measurement, workout_record, sync_cursor

---

## New Schema

All new tables follow the same conventions as existing kasane.db tables:
- TEXT primary keys (UUIDs)
- person_id foreign key linking to person table
- created_at / updated_at timestamps
- date as TEXT in ISO format (YYYY-MM-DD)

### Weight Tracking

```sql
CREATE TABLE IF NOT EXISTS weight_entry (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    weight_lbs REAL NOT NULL,
    waist_in REAL,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_weight_person_date ON weight_entry(person_id, date);
```

### Meals / Nutrition

```sql
CREATE TABLE IF NOT EXISTS meal_entry (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    meal_num INTEGER,
    time_of_day TEXT,
    description TEXT,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    calories REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meal_person_date ON meal_entry(person_id, date);
```

### Blood Pressure

```sql
CREATE TABLE IF NOT EXISTS bp_entry (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    systolic REAL NOT NULL,
    diastolic REAL NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bp_person_date ON bp_entry(person_id, date);
```

### Training Sessions

```sql
CREATE TABLE IF NOT EXISTS training_session (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    rpe REAL,
    duration_min REAL,
    type TEXT,
    name TEXT,
    notes TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_person_date ON training_session(person_id, date);
```

### Strength Sets (detail rows under training_session)

```sql
CREATE TABLE IF NOT EXISTS strength_set (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES training_session(id),
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    exercise TEXT NOT NULL,
    weight_lbs REAL,
    reps INTEGER,
    rpe REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strength_person_date ON strength_set(person_id, date);
```

### Wearable Daily Series

```sql
CREATE TABLE IF NOT EXISTS wearable_daily (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    source TEXT,
    rhr REAL,
    hrv REAL,
    hrv_weekly_avg REAL,
    hrv_status TEXT,
    steps INTEGER,
    sleep_hrs REAL,
    deep_sleep_hrs REAL,
    light_sleep_hrs REAL,
    rem_sleep_hrs REAL,
    awake_hrs REAL,
    sleep_start TEXT,
    sleep_end TEXT,
    calories_total REAL,
    calories_active REAL,
    calories_bmr REAL,
    stress_avg INTEGER,
    floors REAL,
    distance_m REAL,
    max_hr INTEGER,
    min_hr INTEGER,
    vo2_max REAL,
    body_battery INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wearable_person_date ON wearable_daily(person_id, date);
```

### Lab Results

```sql
CREATE TABLE IF NOT EXISTS lab_draw (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    source TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lab_draw_person ON lab_draw(person_id);

CREATE TABLE IF NOT EXISTS lab_result (
    id TEXT PRIMARY KEY,
    draw_id TEXT NOT NULL REFERENCES lab_draw(id),
    person_id TEXT NOT NULL REFERENCES person(id),
    marker TEXT NOT NULL,
    value REAL,
    value_text TEXT,
    unit TEXT,
    reference_low REAL,
    reference_high REAL,
    flag TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lab_result_person_marker ON lab_result(person_id, marker);
```

### Daily Habits

```sql
CREATE TABLE IF NOT EXISTS habit_log (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person(id),
    date TEXT NOT NULL,
    habit_name TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_habit_log_person_date ON habit_log(person_id, date);
```

Note: This replaces the wide daily_habits.csv (20+ columns, one per habit) with a normalized long format (one row per habit per day). Much cleaner for queries.

---

## Model Layer

Python dataclasses in `engine/models/health.py`:

```python
@dataclass
class WeightEntry:
    id: str
    person_id: str
    date: str
    weight_lbs: float
    waist_in: float | None = None
    source: str | None = None

@dataclass
class MealEntry:
    id: str
    person_id: str
    date: str
    meal_num: int | None = None
    time_of_day: str | None = None
    description: str | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    calories: float | None = None
    notes: str | None = None

# ... etc for each table
```

## Controller Layer

`engine/gateway/health_api.py`: New FastAPI router for health data CRUD.

```
POST /api/v1/persons/:id/weight    → create weight entry
GET  /api/v1/persons/:id/weight    → list weight entries (optional ?since=, ?limit=)
POST /api/v1/persons/:id/meals     → create meal entry
GET  /api/v1/persons/:id/meals     → list meals (optional ?date=, ?since=)
POST /api/v1/persons/:id/bp        → create BP entry
GET  /api/v1/persons/:id/bp        → list BP entries
POST /api/v1/persons/:id/sessions  → create training session
GET  /api/v1/persons/:id/sessions  → list sessions
POST /api/v1/persons/:id/labs      → create lab draw + results
GET  /api/v1/persons/:id/labs      → list lab draws with results
```

These follow the same auth pattern as existing v1 endpoints. Per-user token scoping via `_check_person_access`.

## Migration Script

`scripts/migrate_csv_to_sqlite.py`:

1. For each user in `data/users/`:
   - Find matching person record by `health_engine_user_id`
   - Read each CSV/JSON file
   - Insert rows into new SQLite tables (INSERT OR IGNORE for idempotency)
   - Validate row counts match
2. Don't delete CSVs after migration (keep as backup)
3. Run as: `python3 scripts/migrate_csv_to_sqlite.py --user andrew --dry-run`

## Tool Migration

Each MCP tool that reads/writes CSVs gets updated to use SQLite instead. One tool at a time:

| Tool | Current | New |
|---|---|---|
| log_weight | Appends to weight_log.csv | INSERT into weight_entry |
| log_meal | Appends to meal_log.csv | INSERT into meal_entry |
| log_bp | Appends to bp_log.csv | INSERT into bp_entry |
| log_habits | Appends to daily_habits.csv | INSERT into habit_log (normalized) |
| checkin | Reads multiple CSVs | Reads from SQLite tables |
| score | Reads CSVs for profile | Reads from SQLite |
| build_briefing | Reads CSVs | Reads from SQLite |
| get_person_context | Reads CSVs + SQLite | Reads SQLite only |
| pull_garmin | Writes garmin_daily.json | INSERT into wearable_daily |
| log_labs | Writes lab_results.json | INSERT into lab_draw + lab_result |

## Execution Order

1. **Schema:** Add tables to db.py init_db() (no data, just structure)
2. **Models:** Create engine/models/health.py with dataclasses
3. **Migration script:** Write + test with dry-run
4. **Run migration:** For Andrew first, verify data integrity
5. **Update tools one at a time:** Start with log_weight (simplest), verify, then next
6. **Update briefing engine:** Switch CSV reads to SQLite queries
7. **Add v1 API endpoints:** For iOS sync of health data
8. **Remove CSV reads:** Once all tools use SQLite

Each step ships independently and the system works at every intermediate state (CSVs still exist as fallback).
