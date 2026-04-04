"""Tests for supplement and medication SQLite logging.

Covers: SQLite table creation, dual-write from MCP tools, query via SQL.
"""

import uuid
from datetime import datetime, timezone

import pytest

from engine.gateway.db import get_db, init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh SQLite database with schema applied."""
    monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "users" / "andrew").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "kasane.db"
    init_db(str(db_path))
    conn = get_db(str(db_path))

    # Insert a person for testing
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("p1", "Andrew", "andrew", now, now),
    )
    conn.commit()
    return conn


class TestSupplementTable:
    def test_supplement_log_table_exists(self, db):
        """Schema should create supplement_log table."""
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='supplement_log'"
        ).fetchone()
        assert row is not None, "supplement_log table missing from schema"

    def test_insert_supplement(self, db):
        """Can insert a supplement log entry."""
        now = datetime.now(timezone.utc).isoformat()
        rid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO supplement_log (id, person_id, date, name, dose, stack, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, "p1", "2026-04-04", "vitamin_d", "5000 IU", "morning", "mcp", now, now),
        )
        db.commit()

        row = db.execute("SELECT * FROM supplement_log WHERE id = ?", (rid,)).fetchone()
        assert row is not None
        assert row["name"] == "vitamin_d"
        assert row["dose"] == "5000 IU"
        assert row["stack"] == "morning"

    def test_query_supplements_by_date(self, db):
        """Can query supplements for a specific date."""
        now = datetime.now(timezone.utc).isoformat()
        for name, dose in [("vitamin_d", "5000 IU"), ("fish_oil", "2 capsules")]:
            db.execute(
                "INSERT INTO supplement_log (id, person_id, date, name, dose, stack, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "p1", "2026-04-04", name, dose, "morning", "mcp", now, now),
            )
        db.commit()

        rows = db.execute(
            "SELECT name, dose FROM supplement_log WHERE person_id = ? AND date = ?",
            ("p1", "2026-04-04"),
        ).fetchall()
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert names == {"vitamin_d", "fish_oil"}


class TestMedicationTable:
    def test_medication_log_table_exists(self, db):
        """Schema should create medication_log table."""
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='medication_log'"
        ).fetchone()
        assert row is not None, "medication_log table missing from schema"

    def test_insert_medication(self, db):
        """Can insert a medication log entry."""
        now = datetime.now(timezone.utc).isoformat()
        rid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO medication_log (id, person_id, date, name, dose, route, notes, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, "p1", "2026-04-04", "tirzepatide", "2.5mg", "subcutaneous", "injection site: abdomen", "mcp", now, now),
        )
        db.commit()

        row = db.execute("SELECT * FROM medication_log WHERE id = ?", (rid,)).fetchone()
        assert row is not None
        assert row["name"] == "tirzepatide"
        assert row["dose"] == "2.5mg"
        assert row["route"] == "subcutaneous"

    def test_query_medications_by_person(self, db):
        """Can query medication history for a person."""
        now = datetime.now(timezone.utc).isoformat()
        for date in ["2026-04-01", "2026-04-02", "2026-04-03"]:
            db.execute(
                "INSERT INTO medication_log (id, person_id, date, name, dose, route, notes, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "p1", date, "tirzepatide", "2.5mg", "subcutaneous", "", "mcp", now, now),
            )
        db.commit()

        rows = db.execute(
            "SELECT date FROM medication_log WHERE person_id = ? ORDER BY date",
            ("p1",),
        ).fetchall()
        assert len(rows) == 3


class TestToolDualWrite:
    """Verify the MCP tools write to both CSV and SQLite."""

    def test_log_supplements_writes_sqlite(self, db, tmp_path, monkeypatch):
        """log_supplements should write to SQLite when person exists."""
        monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)

        from mcp_server.tools import _log_supplements
        result = _log_supplements(stack="morning", user_id="andrew")
        assert result["logged"] is True

        rows = db.execute(
            "SELECT name, dose, stack FROM supplement_log WHERE person_id = 'p1'"
        ).fetchall()
        assert len(rows) > 0
        names = {r["name"] for r in rows}
        assert "vitamin_d" in names

    def test_log_medication_writes_sqlite(self, db, tmp_path, monkeypatch):
        """log_medication should write to SQLite when person exists."""
        monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)

        from mcp_server.tools import _log_medication
        result = _log_medication(name="tirzepatide", dose="2.5mg", route="subcutaneous", user_id="andrew")
        assert result["logged"] is True

        row = db.execute(
            "SELECT name, dose, route FROM medication_log WHERE person_id = 'p1'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "tirzepatide"
        assert row["dose"] == "2.5mg"
        assert row["route"] == "subcutaneous"

    def test_log_supplements_no_person_still_works(self, db, tmp_path, monkeypatch):
        """log_supplements should still work (CSV only) when no person record exists."""
        monkeypatch.setattr("mcp_server.tools.PROJECT_ROOT", tmp_path)
        (tmp_path / "data" / "users" / "unknown").mkdir(parents=True, exist_ok=True)

        from mcp_server.tools import _log_supplements
        result = _log_supplements(stack="morning", user_id="unknown")
        assert result["logged"] is True

        # No SQLite row since person doesn't exist
        rows = db.execute("SELECT * FROM supplement_log").fetchall()
        assert len(rows) == 0
