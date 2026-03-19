"""Tests for protocol-aware coaching system."""

import tempfile
from pathlib import Path

import yaml

from engine.coaching.protocols import load_protocol, protocol_progress


# --- Fixtures ---

SLEEP_STACK = {
    "name": "Sleep Stack",
    "habits": [
        {"id": "am_sunlight", "label": "Morning sunlight", "priority": 3, "nudge": "Get outside."},
        {"id": "last_meal_2hr", "label": "Last meal 2+ hrs before bed", "priority": 1, "nudge": "Stop eating."},
        {"id": "ac_67", "label": "Room temp ≤67°F", "priority": 2, "nudge": "Cool room."},
    ],
    "phases": [
        {"name": "Building the routine", "weeks": [1, 2], "focus": "Lock in basics."},
        {"name": "Dialing it in", "weeks": [3, 4], "focus": "Fine-tune."},
    ],
    "outcome_metrics": [
        {"id": "sleep_duration_avg", "source": "garmin", "target": 7.5, "unit": "hours"},
    ],
}


def _make_habit_rows(dates_and_values):
    """Build habit data rows from a list of (date, {habit: val}) tuples."""
    rows = []
    for date, habits in dates_and_values:
        row = {"date": date}
        row.update(habits)
        rows.append(row)
    return rows


# --- Tests ---

def test_load_protocol():
    """Load a protocol YAML from disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        proto = {"name": "Test", "habits": [{"id": "x", "priority": 1}], "phases": []}
        path = Path(tmpdir) / "test-proto.yaml"
        with open(path, "w") as f:
            yaml.dump(proto, f)

        loaded = load_protocol("test-proto", protocols_dir=Path(tmpdir))
        assert loaded is not None
        assert loaded["name"] == "Test"


def test_load_protocol_missing():
    """Missing protocol returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        assert load_protocol("nonexistent", protocols_dir=Path(tmpdir)) is None


def test_progress_day_and_week():
    """Day and week numbers computed correctly."""
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        as_of="2026-03-11",
    )
    assert result["day"] == 5
    assert result["week"] == 1


def test_progress_phase_mapping():
    """Correct phase selected based on week number."""
    # Week 1 → phase 1
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        as_of="2026-03-11",
    )
    assert result["phase"] == "Building the routine"

    # Week 3 → phase 2
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        as_of="2026-03-25",
    )
    assert result["phase"] == "Dialing it in"


def test_progress_past_all_phases():
    """Past all defined phases falls back to last phase."""
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-01-01",
        habit_data=[],
        as_of="2026-06-01",
    )
    assert result["phase"] == "Dialing it in"  # last phase


def test_last_night_completion():
    """Habit completion counted from yesterday's row."""
    habit_data = _make_habit_rows([
        ("2026-03-10", {"am_sunlight": "y", "last_meal_2hr": "n", "ac_67": "y"}),
    ])
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=habit_data,
        as_of="2026-03-11",
    )
    assert result["last_night"]["hit"] == 2
    assert result["last_night"]["total"] == 3
    assert "last_meal_2hr" in result["last_night"]["missed"]


def test_nudge_selection():
    """Top nudge is the highest-priority missed habit."""
    habit_data = _make_habit_rows([
        # am_sunlight (priority 3) hit, last_meal_2hr (priority 1) missed, ac_67 (priority 2) missed
        ("2026-03-10", {"am_sunlight": "y", "last_meal_2hr": "n", "ac_67": "n"}),
    ])
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=habit_data,
        as_of="2026-03-11",
    )
    # Priority 1 = last_meal_2hr should be the top nudge
    assert result["top_nudge"]["habit"] == "last_meal_2hr"


def test_no_nudge_when_all_hit():
    """No nudge when all habits completed."""
    habit_data = _make_habit_rows([
        ("2026-03-10", {"am_sunlight": "y", "last_meal_2hr": "y", "ac_67": "y"}),
    ])
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=habit_data,
        as_of="2026-03-11",
    )
    assert result["top_nudge"] is None


def test_empty_habit_data():
    """Gracefully handles empty habit data."""
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        as_of="2026-03-11",
    )
    assert result["last_night"]["hit"] == 0
    assert result["last_night"]["missed"] == ["am_sunlight", "last_meal_2hr", "ac_67"]
    assert result["top_nudge"] is not None


def test_none_habit_data():
    """Gracefully handles None habit data."""
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=None,
        as_of="2026-03-11",
    )
    assert result["last_night"]["hit"] == 0


def test_blank_cells_not_counted():
    """Blank habit values (not tracked) don't count against completion."""
    habit_data = _make_habit_rows([
        ("2026-03-10", {"am_sunlight": "y", "last_meal_2hr": "", "ac_67": "y"}),
    ])
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=habit_data,
        as_of="2026-03-11",
    )
    # Only 2 tracked, both hit
    assert result["last_night"]["hit"] == 2
    assert result["last_night"]["total"] == 2
    assert result["last_night"]["missed"] == []


def test_outcome_metrics_with_garmin():
    """Outcome metrics checked against Garmin data."""
    garmin = {"sleep_duration_avg": 6.8}
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        garmin=garmin,
        as_of="2026-03-11",
    )
    assert len(result["outcomes"]) == 1
    assert result["outcomes"][0]["current"] == 6.8
    assert result["outcomes"][0]["status"] == "below_target"


def test_outcome_metrics_no_garmin():
    """Outcome metrics gracefully handle missing Garmin data."""
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=[],
        garmin=None,
        as_of="2026-03-11",
    )
    assert result["outcomes"][0]["current"] is None
    assert result["outcomes"][0]["status"] is None


def test_phase_avg_completion():
    """Phase average computed across multiple days."""
    habit_data = _make_habit_rows([
        ("2026-03-07", {"am_sunlight": "y", "last_meal_2hr": "y", "ac_67": "y"}),  # 100%
        ("2026-03-08", {"am_sunlight": "y", "last_meal_2hr": "n", "ac_67": "y"}),  # 67%
        ("2026-03-09", {"am_sunlight": "y", "last_meal_2hr": "y", "ac_67": "n"}),  # 67%
    ])
    result = protocol_progress(
        protocol=SLEEP_STACK,
        started="2026-03-07",
        habit_data=habit_data,
        as_of="2026-03-10",
    )
    # avg of 100%, 66.7%, 66.7% ≈ 77.8%
    assert result["phase_avg_completion"] is not None
    assert 77 <= result["phase_avg_completion"] <= 79
