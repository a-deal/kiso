"""Tests for the health priority checkpoint (health_flags module + MCP tool)."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.coaching.health_flags import check_health_priorities, HealthFlag, FlagResult


# ---------------------------------------------------------------------------
# Unit tests: individual flag conditions with boundary values
# ---------------------------------------------------------------------------

class TestGlucoseFlag:
    def test_normal_glucose_no_flag(self):
        result = check_health_priorities(labs={"fasting_glucose": 95})
        assert len(result.flags) == 0

    def test_prediabetic_boundary_100(self):
        result = check_health_priorities(labs={"fasting_glucose": 100})
        assert len(result.flags) == 1
        assert result.flags[0].name == "pre_diabetic_glucose"
        assert result.flags[0].severity == "notable"

    def test_prediabetic_125(self):
        result = check_health_priorities(labs={"fasting_glucose": 125})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_diabetic_boundary_126(self):
        result = check_health_priorities(labs={"fasting_glucose": 126})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_missing_glucose_no_flag(self):
        result = check_health_priorities(labs={})
        assert len(result.flags) == 0


class TestHbA1cFlag:
    def test_normal_hba1c(self):
        result = check_health_priorities(labs={"hba1c": 5.4})
        assert len(result.flags) == 0

    def test_prediabetic_boundary_5_7(self):
        result = check_health_priorities(labs={"hba1c": 5.7})
        assert len(result.flags) == 1
        assert result.flags[0].name == "high_hba1c"
        assert result.flags[0].severity == "notable"

    def test_diabetic_boundary_6_5(self):
        result = check_health_priorities(labs={"hba1c": 6.5})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"


class TestTSHFlag:
    def test_normal_tsh(self):
        result = check_health_priorities(labs={"tsh": 2.0})
        assert len(result.flags) == 0

    def test_low_tsh_notable(self):
        result = check_health_priorities(labs={"tsh": 0.3})
        assert len(result.flags) == 1
        assert result.flags[0].name == "thyroid_abnormal"
        assert result.flags[0].severity == "notable"

    def test_high_tsh_notable(self):
        result = check_health_priorities(labs={"tsh": 5.0})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_very_low_tsh_urgent(self):
        result = check_health_priorities(labs={"tsh": 0.05})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_very_high_tsh_urgent(self):
        result = check_health_priorities(labs={"tsh": 12.0})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_boundary_0_4_no_flag(self):
        result = check_health_priorities(labs={"tsh": 0.4})
        assert len(result.flags) == 0

    def test_boundary_4_0_no_flag(self):
        result = check_health_priorities(labs={"tsh": 4.0})
        assert len(result.flags) == 0

    def test_boundary_0_1_notable(self):
        """0.1 is >= 0.1 so not urgent, but < 0.4 so notable."""
        result = check_health_priorities(labs={"tsh": 0.1})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_boundary_10_notable(self):
        """10.0 is >= 10 so not notable only, but urgent."""
        # Actually > 10 is urgent, == 10 is notable because > 4.0 but not > 10
        result = check_health_priorities(labs={"tsh": 10.0})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"


class TestBloodPressureFlag:
    def test_normal_bp(self):
        result = check_health_priorities(labs={}, bp_systolic=118, bp_diastolic=75)
        assert len(result.flags) == 0

    def test_stage1_systolic(self):
        result = check_health_priorities(labs={}, bp_systolic=130, bp_diastolic=75)
        assert len(result.flags) == 1
        assert result.flags[0].name == "high_blood_pressure"
        assert result.flags[0].severity == "notable"

    def test_stage1_diastolic(self):
        result = check_health_priorities(labs={}, bp_systolic=120, bp_diastolic=80)
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_stage2_systolic(self):
        result = check_health_priorities(labs={}, bp_systolic=140, bp_diastolic=75)
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_stage2_diastolic(self):
        result = check_health_priorities(labs={}, bp_systolic=120, bp_diastolic=90)
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_missing_bp_no_flag(self):
        result = check_health_priorities(labs={})
        assert len(result.flags) == 0

    def test_partial_bp_no_flag(self):
        result = check_health_priorities(labs={}, bp_systolic=140, bp_diastolic=None)
        assert len(result.flags) == 0


class TestTestosteroneFlag:
    def test_low_t_male(self):
        result = check_health_priorities(labs={"testosterone_total": 250}, sex="M")
        assert len(result.flags) == 1
        assert result.flags[0].name == "low_testosterone"
        assert result.flags[0].severity == "notable"

    def test_normal_t_male(self):
        result = check_health_priorities(labs={"testosterone_total": 500}, sex="M")
        assert len(result.flags) == 0

    def test_low_t_female_no_flag(self):
        """Testosterone check only applies to males."""
        result = check_health_priorities(labs={"testosterone_total": 250}, sex="F")
        assert len(result.flags) == 0

    def test_low_t_no_sex_specified(self):
        """When sex is unknown, still check (assume could be male)."""
        result = check_health_priorities(labs={"testosterone_total": 250})
        assert len(result.flags) == 1

    def test_boundary_300_no_flag(self):
        result = check_health_priorities(labs={"testosterone_total": 300}, sex="M")
        assert len(result.flags) == 0


class TestLDLFlag:
    def test_normal_ldl(self):
        result = check_health_priorities(labs={"ldl_c": 120})
        assert len(result.flags) == 0

    def test_high_ldl_notable(self):
        result = check_health_priorities(labs={"ldl_c": 160})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_very_high_ldl_urgent(self):
        result = check_health_priorities(labs={"ldl_c": 190})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"


class TestVitaminDFlag:
    def test_normal_vitamin_d(self):
        result = check_health_priorities(labs={"vitamin_d": 45})
        assert len(result.flags) == 0

    def test_deficient_notable(self):
        result = check_health_priorities(labs={"vitamin_d": 18})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_severe_deficiency_urgent(self):
        result = check_health_priorities(labs={"vitamin_d": 10})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_boundary_20_no_flag(self):
        result = check_health_priorities(labs={"vitamin_d": 20})
        assert len(result.flags) == 0


class TestCRPFlag:
    def test_normal_crp(self):
        result = check_health_priorities(labs={"hscrp": 1.0})
        assert len(result.flags) == 0

    def test_elevated_crp_notable(self):
        result = check_health_priorities(labs={"hscrp": 4.5})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_very_high_crp_urgent(self):
        result = check_health_priorities(labs={"hscrp": 15.0})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_boundary_3_0_no_flag(self):
        result = check_health_priorities(labs={"hscrp": 3.0})
        assert len(result.flags) == 0


class TestEGFRFlag:
    def test_normal_egfr(self):
        result = check_health_priorities(labs={"egfr": 90})
        assert len(result.flags) == 0

    def test_reduced_egfr_notable(self):
        result = check_health_priorities(labs={"egfr": 50})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "notable"

    def test_severe_egfr_urgent(self):
        result = check_health_priorities(labs={"egfr": 25})
        assert len(result.flags) == 1
        assert result.flags[0].severity == "urgent"

    def test_boundary_60_no_flag(self):
        result = check_health_priorities(labs={"egfr": 60})
        assert len(result.flags) == 0


class TestFerritinFlag:
    def test_normal_ferritin(self):
        result = check_health_priorities(labs={"ferritin": 80})
        assert len(result.flags) == 0

    def test_low_ferritin_notable(self):
        result = check_health_priorities(labs={"ferritin": 20})
        assert len(result.flags) == 1
        assert result.flags[0].name == "low_ferritin"
        assert result.flags[0].severity == "notable"

    def test_boundary_30_no_flag(self):
        result = check_health_priorities(labs={"ferritin": 30})
        assert len(result.flags) == 0


# ---------------------------------------------------------------------------
# Goal connection logic
# ---------------------------------------------------------------------------

class TestGoalConnections:
    def test_glucose_with_sleep_goal(self):
        result = check_health_priorities(
            labs={"fasting_glucose": 110},
            current_goal="sleep-better",
        )
        d = result.to_dict()
        assert d["flags"][0]["goal_connection"]
        assert "sleep" in d["flags"][0]["goal_connection"].lower()

    def test_low_t_with_build_strength_goal(self):
        result = check_health_priorities(
            labs={"testosterone_total": 250},
            sex="M",
            current_goal="build-strength",
        )
        d = result.to_dict()
        assert d["flags"][0]["goal_connection"]
        assert "gains" in d["flags"][0]["goal_connection"].lower() or "doctor" in d["flags"][0]["goal_connection"].lower()

    def test_thyroid_with_lose_weight_goal(self):
        result = check_health_priorities(
            labs={"tsh": 6.0},
            current_goal="lose-weight",
        )
        d = result.to_dict()
        assert "metabolism" in d["flags"][0]["goal_connection"].lower()

    def test_no_connection_to_current_goal(self):
        """When flag has no specific connection to the user's goal, suggest course correction."""
        result = check_health_priorities(
            labs={"egfr": 50},
            current_goal="sharper-focus",
        )
        d = result.to_dict()
        assert "more pressing" in d["flags"][0]["goal_connection"].lower()

    def test_no_goal_set(self):
        """When no goal is set, no goal_connection key should appear."""
        result = check_health_priorities(labs={"fasting_glucose": 110})
        d = result.to_dict()
        assert "goal_connection" not in d["flags"][0]


# ---------------------------------------------------------------------------
# Healthy data: no flags
# ---------------------------------------------------------------------------

class TestHealthyData:
    def test_all_normal_no_flags(self):
        """Fully healthy labs + BP should produce zero flags."""
        result = check_health_priorities(
            labs={
                "fasting_glucose": 88,
                "hba1c": 5.2,
                "tsh": 2.1,
                "testosterone_total": 600,
                "ldl_c": 100,
                "vitamin_d": 50,
                "hscrp": 0.8,
                "egfr": 95,
                "ferritin": 120,
            },
            bp_systolic=118,
            bp_diastolic=72,
            sex="M",
            current_goal="sleep-better",
        )
        assert len(result.flags) == 0
        d = result.to_dict()
        assert d["flags_found"] == 0
        assert d["urgent_count"] == 0
        assert d["notable_count"] == 0
        assert "No material findings" in d["suggested_response"]


# ---------------------------------------------------------------------------
# Multiple flags + sorting
# ---------------------------------------------------------------------------

class TestMultipleFlags:
    def test_urgent_sorted_first(self):
        result = check_health_priorities(
            labs={
                "fasting_glucose": 110,  # notable
                "hba1c": 7.0,            # urgent
                "ferritin": 20,          # notable
            },
        )
        d = result.to_dict()
        assert d["flags_found"] == 3
        assert d["urgent_count"] == 1
        assert d["notable_count"] == 2
        # Urgent should be first
        assert d["flags"][0]["severity"] == "urgent"

    def test_suggested_response_urgent(self):
        result = check_health_priorities(labs={"hba1c": 7.0})
        d = result.to_dict()
        assert "Pause" in d["suggested_response"]

    def test_suggested_response_notable_only(self):
        result = check_health_priorities(labs={"ferritin": 20})
        d = result.to_dict()
        assert "Mention" in d["suggested_response"]


# ---------------------------------------------------------------------------
# FlagResult.to_dict structure
# ---------------------------------------------------------------------------

class TestFlagResultDict:
    def test_dict_keys(self):
        result = check_health_priorities(labs={"fasting_glucose": 110}, current_goal="sleep-better")
        d = result.to_dict()
        assert "flags_found" in d
        assert "urgent_count" in d
        assert "notable_count" in d
        assert "current_goal" in d
        assert "has_data" in d
        assert "flags" in d
        assert "suggested_response" in d

    def test_has_data_true_with_labs(self):
        result = check_health_priorities(labs={"fasting_glucose": 88})
        assert result.has_data is True

    def test_has_data_true_with_bp_only(self):
        result = check_health_priorities(labs={}, bp_systolic=120, bp_diastolic=80)
        assert result.has_data is True

    def test_has_data_false_empty(self):
        result = check_health_priorities(labs={})
        assert result.has_data is False


# ---------------------------------------------------------------------------
# MCP tool end-to-end (with mocked data)
# ---------------------------------------------------------------------------

class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

    def resource(self, uri):
        def decorator(fn):
            return fn
        return decorator


@pytest.fixture
def tools():
    from mcp_server.tools import register_tools
    mcp = FakeMCP()
    register_tools(mcp)
    return mcp.tools


@pytest.fixture
def tmp_user(tmp_path, monkeypatch):
    """Set up a temp user directory with config, labs, and BP data."""
    import mcp_server.tools as mod

    def patched_data_dir(user_id=None):
        return tmp_path

    def patched_load_config(user_id=None):
        return {
            "profile": {"age": 35, "sex": "M"},
            "intake": {"goals": ["sleep-better"]},
        }

    monkeypatch.setattr(mod, "_data_dir", patched_data_dir)
    monkeypatch.setattr(mod, "_load_config", patched_load_config)
    return tmp_path


class TestMCPTool:
    def test_tool_registered(self, tools):
        assert "check_health_priorities" in tools

    def test_tool_no_data(self, tools, tmp_user):
        result = tools["check_health_priorities"](user_id="test")
        assert result["flags_found"] == 0
        assert result["has_data"] is False

    def test_tool_with_lab_flags(self, tools, tmp_user):
        # Write lab data with pre-diabetic glucose
        lab_data = {
            "draws": [{"date": "2026-03-20", "source": "Quest", "results": {"fasting_glucose": 115}}],
            "latest": {"fasting_glucose": 115},
        }
        with open(tmp_user / "lab_results.json", "w") as f:
            json.dump(lab_data, f)

        result = tools["check_health_priorities"](user_id="test")
        assert result["flags_found"] == 1
        assert result["flags"][0]["name"] == "pre_diabetic_glucose"
        assert result["flags"][0]["severity"] == "notable"
        # Goal connection to sleep-better should be present
        assert "goal_connection" in result["flags"][0]
        assert "sleep" in result["flags"][0]["goal_connection"].lower()

    def test_tool_with_bp_flags(self, tools, tmp_user):
        # Write BP data
        bp_path = tmp_user / "bp_log.csv"
        bp_path.write_text("date,systolic,diastolic,source\n2026-03-20,145,92,mcp\n")

        result = tools["check_health_priorities"](user_id="test")
        assert result["flags_found"] == 1
        assert result["flags"][0]["name"] == "high_blood_pressure"
        assert result["flags"][0]["severity"] == "urgent"

    def test_tool_with_multiple_flags(self, tools, tmp_user):
        # Labs with multiple issues
        lab_data = {
            "draws": [{"date": "2026-03-20", "source": "Quest", "results": {
                "fasting_glucose": 130,
                "tsh": 0.05,
                "ldl_c": 200,
            }}],
            "latest": {
                "fasting_glucose": 130,
                "tsh": 0.05,
                "ldl_c": 200,
            },
        }
        with open(tmp_user / "lab_results.json", "w") as f:
            json.dump(lab_data, f)

        result = tools["check_health_priorities"](user_id="test")
        assert result["flags_found"] == 3
        assert result["urgent_count"] == 3
        # All should be urgent
        for flag in result["flags"]:
            assert flag["severity"] == "urgent"

    def test_tool_healthy_data_no_flags(self, tools, tmp_user):
        lab_data = {
            "draws": [{"date": "2026-03-20", "source": "Quest", "results": {
                "fasting_glucose": 85,
                "hba1c": 5.1,
                "tsh": 1.8,
                "testosterone_total": 650,
                "ldl_c": 95,
                "vitamin_d": 55,
                "hscrp": 0.5,
                "egfr": 100,
                "ferritin": 90,
            }}],
            "latest": {
                "fasting_glucose": 85,
                "hba1c": 5.1,
                "tsh": 1.8,
                "testosterone_total": 650,
                "ldl_c": 95,
                "vitamin_d": 55,
                "hscrp": 0.5,
                "egfr": 100,
                "ferritin": 90,
            },
        }
        with open(tmp_user / "lab_results.json", "w") as f:
            json.dump(lab_data, f)

        bp_path = tmp_user / "bp_log.csv"
        bp_path.write_text("date,systolic,diastolic,source\n2026-03-20,115,72,mcp\n")

        result = tools["check_health_priorities"](user_id="test")
        assert result["flags_found"] == 0
        assert result["has_data"] is True
        assert "No material findings" in result["suggested_response"]
