"""Tests for briefing score — verifying profile fields flow through to scoring.

Bug: build_briefing() didn't read family_history, medications, phq9_score,
height_inches, or waist_inches from config.yaml into the UserProfile. These
fields were always None, so those metrics always showed as gaps even after
Milo collected the data.

We test at two levels:
1. score_profile() with the fields set — confirms the scoring engine handles them
2. build_briefing()'s profile construction — confirms config.yaml fields are wired
"""

import pytest

from engine.models import Demographics, UserProfile
from engine.scoring.engine import score_profile


def _gap_names(score_output):
    """Extract gap names from score_profile output."""
    return [g.name for g in score_output.get("gaps", [])]


def _result_by_name(score_output, name):
    """Find a specific metric result by name."""
    for r in score_output.get("results", []):
        if r.name == name:
            return r
    return None


# --- Level 1: score_profile produces correct results when fields are set ---

class TestScoreProfileGaps:
    """Verify that setting profile fields closes the corresponding gaps."""

    def _base_profile(self, **overrides):
        demo = Demographics(age=35, sex="M")
        return UserProfile(demographics=demo, **overrides)

    def test_family_history_closes_gap(self):
        without = score_profile(self._base_profile())
        assert "Family History" in _gap_names(without)

        with_fh = score_profile(self._base_profile(
            has_family_history={"maternal": ["cancer"]}
        ))
        assert "Family History" not in _gap_names(with_fh)

    def test_medication_list_closes_gap(self):
        without = score_profile(self._base_profile())
        assert "Medication List" in _gap_names(without)

        with_meds = score_profile(self._base_profile(has_medication_list=True))
        assert "Medication List" not in _gap_names(with_meds)

    def test_phq9_closes_gap(self):
        without = score_profile(self._base_profile())
        assert "PHQ-9 (Depression)" in _gap_names(without)

        with_phq9 = score_profile(self._base_profile(phq9_score=3))
        assert "PHQ-9 (Depression)" not in _gap_names(with_phq9)

    def test_whtr_scores_when_height_and_waist_set(self):
        # weight_lbs required for the unit string
        profile = self._base_profile(
            height_inches=70, waist_circumference=35.5, weight_lbs=188
        )
        result = score_profile(profile)
        wt = _result_by_name(result, "Weight Trends")
        assert wt is not None, "Weight Trends should appear in results"
        assert wt.value == pytest.approx(0.507, abs=0.001)
        assert "WHtR" in wt.unit

    def test_whtr_without_weight_lbs(self):
        """WHtR should still work even without weight_lbs (unit string adapts)."""
        profile = self._base_profile(height_inches=70, waist_circumference=35.5)
        result = score_profile(profile)
        wt = _result_by_name(result, "Weight Trends")
        assert wt is not None
        assert wt.value == pytest.approx(0.507, abs=0.001)

    def test_all_three_gaps_close(self):
        profile = self._base_profile(
            has_family_history={"maternal": ["cancer"]},
            has_medication_list=True,
            phq9_score=3,
        )
        result = score_profile(profile)
        gaps = _gap_names(result)
        for name in ("Family History", "Medication List", "PHQ-9 (Depression)"):
            assert name not in gaps, f"{name} should NOT be a gap"

    def test_coverage_increases(self):
        without = score_profile(self._base_profile())
        with_all = score_profile(self._base_profile(
            has_family_history={"maternal": ["cancer"]},
            has_medication_list=True,
            phq9_score=3,
        ))
        assert with_all["coverage_score"] > without["coverage_score"], \
            f"Coverage should increase: {without['coverage_score']} -> {with_all['coverage_score']}"


# --- Level 2: build_briefing wires config fields into UserProfile ---

class TestBriefingProfileWiring:
    """Verify build_briefing reads config fields into the UserProfile.

    We patch score_profile to capture the UserProfile it receives,
    then check the fields were set from config. We also mock db_read
    functions since there's no real database in tests.
    """

    def _capture_profile(self, config, monkeypatch):
        """Run build_briefing and capture the UserProfile passed to score_profile."""
        captured = {}

        original_score_profile = score_profile

        def spy(profile, **kwargs):
            captured["profile"] = profile
            return original_score_profile(profile, **kwargs)

        monkeypatch.setattr("engine.coaching.briefing.score_profile", spy)

        # Mock db_read functions that require a real database
        monkeypatch.setattr("engine.coaching.briefing.get_bp", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_weights", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_meals", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_habits", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_sleep", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_strength", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_labs", lambda *a, **kw: [])
        monkeypatch.setattr("engine.coaching.briefing.get_wearable_daily", lambda *a, **kw: [])

        from engine.coaching.briefing import build_briefing
        build_briefing(config)
        return captured.get("profile")

    def _make_config(self, tmp_path, profile_overrides=None):
        data_dir = tmp_path / "data" / "users" / "testuser"
        data_dir.mkdir(parents=True, exist_ok=True)
        profile = {"age": 35, "sex": "M"}
        if profile_overrides:
            profile.update(profile_overrides)
        return {"data_dir": str(data_dir), "profile": profile, "targets": {}}

    def test_family_history_wired(self, tmp_path, monkeypatch):
        config = self._make_config(tmp_path, {
            "family_history": {"maternal": ["cancer"], "paternal": ["heart disease"]}
        })
        profile = self._capture_profile(config, monkeypatch)
        assert profile.has_family_history is not None, \
            "build_briefing should set has_family_history from config"

    def test_medication_list_wired(self, tmp_path, monkeypatch):
        config = self._make_config(tmp_path, {"medications": ["lisinopril 10mg"]})
        profile = self._capture_profile(config, monkeypatch)
        assert profile.has_medication_list is True, \
            "build_briefing should set has_medication_list from config"

    def test_phq9_wired(self, tmp_path, monkeypatch):
        config = self._make_config(tmp_path, {"phq9_score": 3})
        profile = self._capture_profile(config, monkeypatch)
        assert profile.phq9_score == 3, \
            "build_briefing should set phq9_score from config"

    def test_waist_and_height_wired(self, tmp_path, monkeypatch):
        config = self._make_config(tmp_path, {
            "height_inches": 70, "waist_inches": 35.5
        })
        profile = self._capture_profile(config, monkeypatch)
        assert profile.height_inches == 70, \
            "build_briefing should set height_inches from config"
        assert profile.waist_circumference == 35.5, \
            "build_briefing should set waist_circumference from config"

    def test_none_when_not_in_config(self, tmp_path, monkeypatch):
        config = self._make_config(tmp_path)
        profile = self._capture_profile(config, monkeypatch)
        assert profile.has_family_history is None
        assert profile.has_medication_list is None
        assert profile.phq9_score is None
        assert profile.height_inches is None
        assert profile.waist_circumference is None
