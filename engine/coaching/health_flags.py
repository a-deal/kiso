"""Health priority checkpoint — flags material findings when new data arrives.

Each flag defines a condition checked against available health data (labs, BP,
wearable metrics, weight). When a flag fires, it includes severity, a
coaching-appropriate message, and connections to common health goals so the
coach can contextualize the finding relative to whatever the user is working on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HealthFlag:
    """A single red-flag definition."""
    name: str
    severity: str  # "urgent" or "notable"
    message: str   # coaching-appropriate language
    goal_connections: dict[str, str] = field(default_factory=dict)


@dataclass
class FlagResult:
    """Result of running the checkpoint against a user's data."""
    flags: list[HealthFlag]
    current_goal: str | None
    has_data: bool  # whether any health data was available at all

    def to_dict(self) -> dict:
        items = []
        for f in self.flags:
            entry: dict = {
                "name": f.name,
                "severity": f.severity,
                "message": f.message,
            }
            if self.current_goal and self.current_goal in f.goal_connections:
                entry["goal_connection"] = f.goal_connections[self.current_goal]
            elif self.current_goal:
                entry["goal_connection"] = (
                    f"This finding may be more pressing than your current "
                    f"'{self.current_goal}' goal. Worth discussing with your doctor "
                    f"before optimizing other areas."
                )
            items.append(entry)

        urgent = [f for f in items if f["severity"] == "urgent"]
        notable = [f for f in items if f["severity"] == "notable"]

        if urgent:
            suggested_response = (
                "Pause current coaching focus. Surface these findings to the user "
                "and strongly recommend they discuss with their doctor before "
                "continuing with habit work."
            )
        elif notable:
            suggested_response = (
                "Mention these findings naturally in the next check-in. Frame them "
                "as worth discussing with a doctor, but don't derail the current "
                "habit focus unless the user wants to."
            )
        else:
            suggested_response = "No material findings. Continue current coaching plan."

        return {
            "flags_found": len(self.flags),
            "urgent_count": len(urgent),
            "notable_count": len(notable),
            "current_goal": self.current_goal,
            "has_data": self.has_data,
            "flags": items,
            "suggested_response": suggested_response,
        }


# ---------------------------------------------------------------------------
# Goal connection templates
# ---------------------------------------------------------------------------

_GOAL_CONNECTIONS: dict[str, dict[str, str]] = {
    "pre_diabetic_glucose": {
        "sleep-better": (
            "Sleep directly impacts insulin sensitivity. Your sleep work is "
            "even more important given this glucose reading."
        ),
        "lose-weight": (
            "Weight loss improves insulin sensitivity. Your current goal "
            "directly addresses this finding."
        ),
        "eat-healthier": (
            "Nutrition choices have a direct impact on blood sugar. Your "
            "current focus is well-aligned with improving this number."
        ),
        "more-energy": (
            "Blood sugar swings can cause energy crashes. Getting glucose "
            "under control may be the key to your energy goal."
        ),
        "build-strength": (
            "Insulin resistance can impair muscle protein synthesis. Worth "
            "addressing alongside your strength work."
        ),
    },
    "high_hba1c": {
        "sleep-better": (
            "Poor sleep raises HbA1c independent of diet. Your sleep work "
            "directly supports better glucose control."
        ),
        "lose-weight": (
            "Weight loss is one of the most effective ways to lower HbA1c. "
            "Your current goal directly addresses this."
        ),
        "eat-healthier": (
            "Sustained nutrition changes are the primary lever for HbA1c. "
            "Your current focus is exactly right."
        ),
    },
    "thyroid_abnormal": {
        "lose-weight": (
            "Thyroid function directly affects metabolism. Worth investigating "
            "before optimizing nutrition alone."
        ),
        "more-energy": (
            "Thyroid dysfunction is one of the most common causes of fatigue. "
            "This could be the root cause of your energy issues."
        ),
        "better-mood": (
            "Thyroid imbalance is strongly linked to mood changes. This is "
            "worth investigating as a potential driver."
        ),
        "build-strength": (
            "Thyroid hormones regulate metabolic rate and can affect recovery. "
            "Worth ruling out before assuming training issues."
        ),
    },
    "high_blood_pressure": {
        "less-stress": (
            "Stress management directly lowers blood pressure. Your current "
            "focus is addressing this finding."
        ),
        "lose-weight": (
            "Weight loss is highly effective for BP reduction. Your current "
            "goal directly supports this."
        ),
        "sleep-better": (
            "Sleep apnea and poor sleep quality are major drivers of high "
            "blood pressure. Your sleep work may help here."
        ),
        "more-energy": (
            "Uncontrolled BP can cause fatigue. Getting this addressed may "
            "help your energy levels."
        ),
    },
    "low_testosterone": {
        "build-strength": (
            "This could be limiting your gains. Worth discussing with your "
            "doctor before assuming your program is the issue."
        ),
        "lose-weight": (
            "Low T makes fat loss harder and can increase abdominal fat. "
            "Worth investigating alongside your weight goal."
        ),
        "more-energy": (
            "Low testosterone is a common cause of fatigue. This could be "
            "the root of your energy concerns."
        ),
        "better-mood": (
            "Low testosterone is associated with depression and irritability. "
            "Worth investigating as a contributing factor."
        ),
        "sleep-better": (
            "Low T and poor sleep feed each other. Improving sleep can raise "
            "testosterone, and vice versa."
        ),
    },
    "high_ldl": {
        "eat-healthier": (
            "Dietary changes can meaningfully reduce LDL. Your nutrition "
            "focus can help here."
        ),
        "lose-weight": (
            "Weight loss often improves lipid profiles. Your current goal "
            "may help bring LDL down."
        ),
    },
    "low_vitamin_d": {
        "build-strength": (
            "Vitamin D deficiency impairs muscle function and recovery. "
            "Supplementation could support your strength goals."
        ),
        "better-mood": (
            "Low vitamin D is linked to depression. Correcting this may "
            "directly support your mood goal."
        ),
        "more-energy": (
            "Vitamin D deficiency causes fatigue. This could be contributing "
            "to your energy issues."
        ),
        "sleep-better": (
            "Vitamin D plays a role in sleep quality. Correcting deficiency "
            "may support your sleep goals."
        ),
    },
    "high_crp": {
        "lose-weight": (
            "Excess body fat drives systemic inflammation. Your weight loss "
            "goal directly addresses this."
        ),
        "sleep-better": (
            "Poor sleep increases inflammation. Your sleep work can help "
            "bring CRP down."
        ),
        "less-stress": (
            "Chronic stress drives inflammation. Your stress management "
            "work supports this finding."
        ),
        "eat-healthier": (
            "Diet is a major driver of systemic inflammation. Your nutrition "
            "focus can help address this."
        ),
    },
    "low_egfr": {
        "lose-weight": (
            "Maintaining a healthy weight supports kidney function. "
            "Your current goal is aligned."
        ),
    },
    "low_ferritin": {
        "more-energy": (
            "Low iron stores are a top cause of fatigue. Correcting this "
            "could significantly improve your energy."
        ),
        "build-strength": (
            "Iron is essential for oxygen delivery to muscles. Low ferritin "
            "can limit training performance and recovery."
        ),
    },
}


# ---------------------------------------------------------------------------
# Flag checker functions
# ---------------------------------------------------------------------------

def _check_glucose(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("fasting_glucose")
    if val is None:
        return None
    if val >= 126:
        return HealthFlag(
            name="pre_diabetic_glucose",
            severity="urgent",
            message=(
                f"Fasting glucose is {val} mg/dL. That's in the diabetic range "
                f"(>=126). This needs a conversation with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["pre_diabetic_glucose"],
        )
    if val >= 100:
        return HealthFlag(
            name="pre_diabetic_glucose",
            severity="notable",
            message=(
                f"Fasting glucose is {val} mg/dL, which is in the pre-diabetic "
                f"range (100-125). Worth monitoring and discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["pre_diabetic_glucose"],
        )
    return None


def _check_hba1c(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("hba1c")
    if val is None:
        return None
    if val >= 6.5:
        return HealthFlag(
            name="high_hba1c",
            severity="urgent",
            message=(
                f"HbA1c is {val}%, which is in the diabetic range (>=6.5%). "
                f"This needs a conversation with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_hba1c"],
        )
    if val >= 5.7:
        return HealthFlag(
            name="high_hba1c",
            severity="notable",
            message=(
                f"HbA1c is {val}%, which is in the pre-diabetic range "
                f"(5.7-6.4%). Worth monitoring and discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_hba1c"],
        )
    return None


def _check_tsh(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("tsh")
    if val is None:
        return None
    if val < 0.1 or val > 10:
        return HealthFlag(
            name="thyroid_abnormal",
            severity="urgent",
            message=(
                f"TSH is {val} mIU/L, which is significantly outside normal "
                f"range. This needs a conversation with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["thyroid_abnormal"],
        )
    if val < 0.4 or val > 4.0:
        return HealthFlag(
            name="thyroid_abnormal",
            severity="notable",
            message=(
                f"TSH is {val} mIU/L, which is outside the normal range "
                f"(0.4-4.0). Worth following up with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["thyroid_abnormal"],
        )
    return None


def _check_blood_pressure(bp_systolic: float | None, bp_diastolic: float | None, **_) -> HealthFlag | None:
    if bp_systolic is None or bp_diastolic is None:
        return None
    if bp_systolic >= 140 or bp_diastolic >= 90:
        return HealthFlag(
            name="high_blood_pressure",
            severity="urgent",
            message=(
                f"Blood pressure is {int(bp_systolic)}/{int(bp_diastolic)} mmHg. "
                f"That's stage 2 hypertension. This needs a conversation with "
                f"your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_blood_pressure"],
        )
    if bp_systolic >= 130 or bp_diastolic >= 80:
        return HealthFlag(
            name="high_blood_pressure",
            severity="notable",
            message=(
                f"Blood pressure is {int(bp_systolic)}/{int(bp_diastolic)} mmHg. "
                f"That's elevated (stage 1 hypertension range). Worth monitoring "
                f"and discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_blood_pressure"],
        )
    return None


def _check_testosterone(labs: dict, sex: str | None = None, **_) -> HealthFlag | None:
    if sex and sex.upper() != "M":
        return None
    val = labs.get("testosterone_total")
    if val is None:
        return None
    if val < 300:
        return HealthFlag(
            name="low_testosterone",
            severity="notable",
            message=(
                f"Total testosterone is {val} ng/dL, which is below the "
                f"clinical threshold of 300. Worth discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_testosterone"],
        )
    return None


def _check_ldl(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("ldl_c")
    if val is None:
        return None
    if val >= 190:
        return HealthFlag(
            name="high_ldl",
            severity="urgent",
            message=(
                f"LDL cholesterol is {val} mg/dL. Values >=190 significantly "
                f"increase cardiovascular risk. This needs a conversation with "
                f"your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_ldl"],
        )
    if val >= 160:
        return HealthFlag(
            name="high_ldl",
            severity="notable",
            message=(
                f"LDL cholesterol is {val} mg/dL, which is elevated (>=160). "
                f"Worth discussing with your doctor, especially with other risk "
                f"factors."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_ldl"],
        )
    return None


def _check_vitamin_d(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("vitamin_d")
    if val is None:
        return None
    if val < 12:
        return HealthFlag(
            name="low_vitamin_d",
            severity="urgent",
            message=(
                f"Vitamin D is {val} ng/mL. Severe deficiency (<12) can cause "
                f"bone loss, muscle weakness, and immune dysfunction. Needs "
                f"medical attention."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_vitamin_d"],
        )
    if val < 20:
        return HealthFlag(
            name="low_vitamin_d",
            severity="notable",
            message=(
                f"Vitamin D is {val} ng/mL, which is deficient (<20). "
                f"Supplementation is recommended. Discuss dosing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_vitamin_d"],
        )
    return None


def _check_crp(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("hscrp")
    if val is None:
        return None
    if val > 10:
        return HealthFlag(
            name="high_crp",
            severity="urgent",
            message=(
                f"hs-CRP is {val} mg/L. Values >10 may indicate acute infection "
                f"or significant inflammation. This needs medical evaluation."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_crp"],
        )
    if val > 3.0:
        return HealthFlag(
            name="high_crp",
            severity="notable",
            message=(
                f"hs-CRP is {val} mg/L, which indicates elevated systemic "
                f"inflammation (>3.0). Worth monitoring and discussing with "
                f"your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["high_crp"],
        )
    return None


def _check_egfr(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("egfr")
    if val is None:
        return None
    if val < 30:
        return HealthFlag(
            name="low_egfr",
            severity="urgent",
            message=(
                f"eGFR is {val} mL/min, indicating severely reduced kidney "
                f"function (<30). This needs urgent medical attention."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_egfr"],
        )
    if val < 60:
        return HealthFlag(
            name="low_egfr",
            severity="notable",
            message=(
                f"eGFR is {val} mL/min, indicating reduced kidney function "
                f"(<60). Worth discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_egfr"],
        )
    return None


def _check_ferritin(labs: dict, **_) -> HealthFlag | None:
    val = labs.get("ferritin")
    if val is None:
        return None
    if val < 30:
        return HealthFlag(
            name="low_ferritin",
            severity="notable",
            message=(
                f"Ferritin is {val} ng/mL, which indicates low iron stores "
                f"(<30). Can cause fatigue, poor recovery, and reduced exercise "
                f"tolerance. Worth discussing with your doctor."
            ),
            goal_connections=_GOAL_CONNECTIONS["low_ferritin"],
        )
    return None


# Ordered list of all checkers
_FLAG_CHECKERS: list[Callable] = [
    _check_glucose,
    _check_hba1c,
    _check_tsh,
    _check_blood_pressure,
    _check_testosterone,
    _check_ldl,
    _check_vitamin_d,
    _check_crp,
    _check_egfr,
    _check_ferritin,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_health_priorities(
    labs: dict,
    bp_systolic: float | None = None,
    bp_diastolic: float | None = None,
    sex: str | None = None,
    current_goal: str | None = None,
) -> FlagResult:
    """Run all health flag checks against available data.

    Args:
        labs: Dict of normalized lab keys to float values (e.g. from latest).
        bp_systolic: Most recent systolic BP reading.
        bp_diastolic: Most recent diastolic BP reading.
        sex: "M" or "F" (affects testosterone check).
        current_goal: The user's current coaching goal (e.g. "sleep-better").

    Returns:
        FlagResult with all flags found, sorted urgent-first.
    """
    has_data = bool(labs) or bp_systolic is not None

    flags: list[HealthFlag] = []
    kwargs = {
        "labs": labs,
        "bp_systolic": bp_systolic,
        "bp_diastolic": bp_diastolic,
        "sex": sex,
    }

    for checker in _FLAG_CHECKERS:
        result = checker(**kwargs)
        if result is not None:
            flags.append(result)

    # Sort: urgent first, then notable
    severity_order = {"urgent": 0, "notable": 1}
    flags.sort(key=lambda f: severity_order.get(f.severity, 2))

    return FlagResult(
        flags=flags,
        current_goal=current_goal,
        has_data=has_data,
    )
