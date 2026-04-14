"""Guard: _user_dir refuses to create new directories for unknown slugs.

Background: _user_dir was eagerly mkdir'ing data/users/<slug>/ on demand for
any slug passed to it. A typo in a CLI flag or a test invocation would
silently materialize a ghost user directory. This test pins the invariant:

    - Existing directories pass through unchanged (backward compat).
    - New directory creation requires a matching person row.
    - Unknown slug raises ValueError naming the slug.

Without this guard, the data_dir -> person resolver's auto-create logic
(or any read-path with briefing) would normalize garbage into the person
table. The person record is the source of truth; the directory follows.

Paired with tests/test_user_issues.py for the person-row insertion pattern.
"""

from datetime import datetime, timezone

import pytest

from engine.gateway.db import close_db, get_db, init_db


@pytest.fixture
def sandboxed_project(tmp_path, monkeypatch):
    """Point PROJECT_ROOT at tmp_path and seed a person row for 'andrew'."""
    import mcp_server.tools as tools

    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)

    close_db()
    db_path = tmp_path / "data" / "kasane.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    conn = get_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO person (id, name, health_engine_user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("a1", "Andrew", "andrew", now, now),
    )
    conn.commit()

    yield tmp_path

    close_db()


class TestUserDirGuard:
    def test_known_slug_creates_dir(self, sandboxed_project):
        """A slug with a matching person row may create its directory."""
        from mcp_server.tools import _user_dir

        path = _user_dir("andrew")
        assert path == sandboxed_project / "data" / "users" / "andrew"
        assert path.exists()
        assert path.is_dir()

    def test_unknown_slug_raises(self, sandboxed_project):
        """A slug with no person row must not create a ghost directory."""
        from mcp_server.tools import _user_dir

        with pytest.raises(ValueError) as excinfo:
            _user_dir("ghost_user_xyz")

        # Error message must name the slug for debuggability.
        assert "ghost_user_xyz" in str(excinfo.value)

        # And the directory must not exist.
        ghost_dir = sandboxed_project / "data" / "users" / "ghost_user_xyz"
        assert not ghost_dir.exists()

    def test_existing_dir_passes_through_without_db_check(
        self, sandboxed_project, tmp_path
    ):
        """Pre-existing directories return without requiring a person row.

        Backward compat: legacy users who already have a data/users/<slug>/
        directory keep working. The guard only fires on new directory creation.
        Commit 1 of this session nukes unwanted legacy dirs; this test proves
        we don't break the legitimate ones in the process.
        """
        from mcp_server.tools import _user_dir

        # Pre-create a dir for a slug that has NO person row.
        legacy_dir = sandboxed_project / "data" / "users" / "legacy_no_person"
        legacy_dir.mkdir(parents=True)

        # _user_dir must return it without raising.
        path = _user_dir("legacy_no_person")
        assert path == legacy_dir
