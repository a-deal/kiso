"""Pin WAL pragmas for the gateway db connection.

Background: kasane.db's WAL grew to 4.1 MB while the db itself was
2.2 MB because nothing was triggering checkpoints. The journal mode
was already WAL, but wal_autocheckpoint was at SQLite's default of 1000
pages — and the connection was being held open by long-lived processes
in a way that meant the autocheckpoint guard was never asserted.
This test pins both pragmas so a future "tune the database" PR can't
silently disable them. Milestone 1 of the baseline consolidation sprint.
"""

from engine.gateway.db import close_db, get_db


def test_journal_mode_is_wal(tmp_path):
    close_db()
    db_path = tmp_path / "test.db"
    conn = get_db(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    close_db()


def test_wal_autocheckpoint_is_set(tmp_path):
    close_db()
    db_path = tmp_path / "test.db"
    conn = get_db(db_path)
    pages = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    assert pages == 1000, (
        f"wal_autocheckpoint should be pinned to 1000, got {pages}. "
        "Without this the WAL can balloon past the database file."
    )
    close_db()
