"""Tests for setup_profile — partial updates and briefing rebuild.

Bug: setup_profile required age+sex on every call, so Milo couldn't
just add medications or PHQ-9 without re-passing everything. Also,
after storing data, briefing.json was never rebuilt, so the dashboard
showed stale scores.
"""

import json
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

from mcp_server.tools import _setup_profile, _config_path, _data_dir


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """Set up a temp user directory with initial config."""
    data_root = tmp_path / "data" / "users" / "testuser"
    data_root.mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Initial config with age and sex
    config_path = config_dir / "testuser" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    initial = {"profile": {"age": 35, "sex": "M", "name": "Test"}, "targets": {}}
    with open(config_path, "w") as f:
        yaml.dump(initial, f)

    # Patch _config_path and _data_dir to use temp dirs
    monkeypatch.setattr("mcp_server.tools._config_path",
                        lambda uid=None: config_path)
    monkeypatch.setattr("mcp_server.tools._data_dir",
                        lambda uid=None: data_root)

    # Patch build_briefing to avoid needing real data files
    # The lazy import in _setup_profile does `from engine.coaching.briefing import build_briefing`
    monkeypatch.setattr("engine.coaching.briefing.build_briefing",
                        lambda cfg: {"score": {"coverage": 99}})

    return {"config_path": config_path, "data_dir": data_root}


class TestPartialUpdate:
    """setup_profile should allow updating individual fields without re-passing age/sex."""

    def test_add_medications_without_age_sex(self, user_dir):
        """Milo can call setup_profile(medications='none') without age/sex."""
        result = _setup_profile(medications="none", user_id="testuser")
        assert result["saved"]

        with open(user_dir["config_path"]) as f:
            cfg = yaml.safe_load(f)

        # medications stored
        assert cfg["profile"]["medications"] == "none"
        # age and sex preserved from initial config
        assert cfg["profile"]["age"] == 35
        assert cfg["profile"]["sex"] == "M"

    def test_add_phq9_without_age_sex(self, user_dir):
        """Milo can call setup_profile(phq9_score=3) without age/sex."""
        result = _setup_profile(phq9_score=3, user_id="testuser")
        assert result["saved"]

        with open(user_dir["config_path"]) as f:
            cfg = yaml.safe_load(f)

        assert cfg["profile"]["phq9_score"] == 3
        assert cfg["profile"]["age"] == 35

    def test_add_family_history_without_age_sex(self, user_dir):
        result = _setup_profile(
            family_history={"maternal": ["cancer"]},
            user_id="testuser",
        )
        assert result["saved"]

        with open(user_dir["config_path"]) as f:
            cfg = yaml.safe_load(f)

        assert cfg["profile"]["family_history"] == {"maternal": ["cancer"]}
        assert cfg["profile"]["age"] == 35

    def test_multiple_fields_at_once(self, user_dir):
        """Can update medications and PHQ-9 in one call."""
        result = _setup_profile(
            medications="lisinopril 10mg",
            phq9_score=3,
            waist_inches=35.5,
            user_id="testuser",
        )
        assert result["saved"]

        with open(user_dir["config_path"]) as f:
            cfg = yaml.safe_load(f)

        assert cfg["profile"]["medications"] == "lisinopril 10mg"
        assert cfg["profile"]["phq9_score"] == 3
        assert cfg["profile"]["waist_inches"] == 35.5
        assert cfg["profile"]["age"] == 35
        assert cfg["profile"]["sex"] == "M"

    def test_age_sex_still_works_when_passed(self, user_dir):
        """Passing age/sex still updates them."""
        result = _setup_profile(age=36, sex="M", user_id="testuser")

        with open(user_dir["config_path"]) as f:
            cfg = yaml.safe_load(f)

        assert cfg["profile"]["age"] == 36


class TestBriefingRebuild:
    """setup_profile should rebuild briefing.json after saving config."""

    def test_briefing_json_created(self, user_dir):
        """After setup_profile, briefing.json should exist in data dir."""
        _setup_profile(medications="none", user_id="testuser")

        briefing_path = user_dir["data_dir"] / "briefing.json"
        assert briefing_path.exists(), "briefing.json should be created after setup_profile"

        with open(briefing_path) as f:
            briefing = json.load(f)
        assert "score" in briefing

    def test_briefing_refreshed_flag(self, user_dir):
        """Return value should indicate briefing was refreshed."""
        result = _setup_profile(phq9_score=3, user_id="testuser")
        assert result.get("briefing_refreshed") is True
