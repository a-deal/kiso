"""Higher-level coaching signals — sleep debt, deficit impact, taper logic.

These go beyond threshold-based insights and look at compound effects
across multiple data streams.
"""

from typing import Optional

from engine.models import Insight


def assess_sleep_debt(
    sleep_hrs_avg: Optional[float],
    sleep_target: float = 7.0,
    days: int = 7,
) -> Optional[Insight]:
    """Estimate cumulative sleep debt over a period."""
    if sleep_hrs_avg is None:
        return None
    debt = (sleep_target - sleep_hrs_avg) * days
    if debt <= 0:
        return None
    if debt > 7:
        return Insight(
            severity="critical", category="sleep",
            title=f"~{debt:.0f}hr sleep debt accumulated this week",
            body=f"Averaging {sleep_hrs_avg:.1f}hrs vs {sleep_target}hr target = "
                 f"~{debt:.0f}hr debt over {days} days. This compounds — "
                 f"recovery capacity, training quality, and hunger signals all degrade. "
                 f"Prioritize 1-2 catch-up nights before pushing training.",
        )
    elif debt > 3.5:
        return Insight(
            severity="warning", category="sleep",
            title=f"~{debt:.1f}hr sleep debt this week",
            body=f"Averaging {sleep_hrs_avg:.1f}hrs vs {sleep_target}hr target. "
                 f"Not critical yet, but the deficit erodes HRV and willpower over time.",
        )
    return None


def assess_deficit_impact(
    weekly_loss_rate: Optional[float],
    hrv: Optional[float],
    rhr: Optional[float],
    weeks_in_deficit: Optional[int] = None,
) -> Optional[Insight]:
    """Assess whether the caloric deficit is sustainable given recovery markers."""
    if weekly_loss_rate is None:
        return None

    signals = []
    if hrv is not None and hrv < 55:
        signals.append(f"HRV at {hrv:.0f}ms (below 55)")
    if rhr is not None and rhr > 55:
        signals.append(f"RHR at {rhr:.0f}bpm (above 55)")

    if weekly_loss_rate > 2.0 and signals:
        body = (f"Losing {weekly_loss_rate:.1f} lbs/week with {' and '.join(signals)}. "
                f"The deficit may be too aggressive for current recovery capacity.")
        if weeks_in_deficit and weeks_in_deficit > 8:
            body += f" After {weeks_in_deficit} weeks in a deficit, fatigue accumulates — consider a diet break."
        return Insight(
            severity="critical", category="weight",
            title="Deficit may be unsustainable",
            body=body,
        )

    if weeks_in_deficit and weeks_in_deficit > 10 and not signals:
        return Insight(
            severity="neutral", category="weight",
            title=f"Week {weeks_in_deficit} of deficit — recovery holding",
            body=f"Recovery markers are stable through week {weeks_in_deficit}. "
                 f"If strength is maintained, current approach is working. "
                 f"Plan a transition to maintenance in the next 2-4 weeks.",
        )
    return None


def assess_sleep_deficit_interaction(
    sleep_hrs_avg: Optional[float],
    sleep_regularity: Optional[float] = None,
    weekly_loss_rate: Optional[float] = None,
    hrv: Optional[float] = None,
) -> Optional[Insight]:
    """Assess compound effect of poor sleep on a caloric deficit.

    Sleep restriction during a deficit shifts weight loss toward lean mass
    (Nedeltcheva 2010: 60% more muscle lost, 55% less fat lost). This signal
    fires when sleep quality metrics combine with an active deficit to create
    muscle-loss risk.
    """
    if sleep_hrs_avg is None or weekly_loss_rate is None:
        return None
    if sleep_hrs_avg >= 7.0 or weekly_loss_rate <= 0:
        return None

    # Base condition: short sleep + active deficit
    risk_factors = []
    risk_factors.append(f"{sleep_hrs_avg:.1f}hr avg sleep")
    risk_factors.append(f"{weekly_loss_rate:.1f} lbs/week deficit")

    severity = "warning"
    body_parts = [
        f"Averaging {sleep_hrs_avg:.1f}hrs sleep with a {weekly_loss_rate:.1f} lbs/week deficit. "
        f"Research shows sleep-restricted dieters lose 60% more muscle and 55% less fat "
        f"at the same caloric deficit (Nedeltcheva 2010)."
    ]

    # Regularity compounds the effect
    if sleep_regularity is not None and sleep_regularity > 60:
        risk_factors.append(f"\u00b1{sleep_regularity:.0f}min bedtime variance")
        body_parts.append(
            f"Bedtime variance of \u00b1{sleep_regularity:.0f}min further disrupts "
            f"circadian cortisol rhythm, compounding the partitioning problem."
        )

    # HRV tells us about recovery capacity
    if hrv is not None:
        if hrv < 55:
            severity = "critical"
            risk_factors.append(f"HRV at {hrv:.0f}ms")
            body_parts.append(
                f"HRV at {hrv:.0f}ms confirms the stress cascade is measurable — "
                f"poor sleep \u2192 elevated cortisol \u2192 suppressed recovery \u2192 muscle loss. "
                f"Consider pausing the deficit until sleep stabilizes above 7hrs."
            )
        elif hrv > 60:
            body_parts.append(
                f"HRV at {hrv:.0f}ms suggests recovery is holding for now, "
                f"but this buffer erodes quickly under sustained sleep debt."
            )

    # Aggressive deficit compounds further
    if weekly_loss_rate > 0.8:
        if severity != "critical":
            severity = "warning"
        body_parts.append(
            f"At >{weekly_loss_rate:.1f} lbs/week, the deficit is aggressive enough "
            f"that sleep quality becomes the deciding factor in what you lose."
        )

    return Insight(
        severity=severity,
        category="sleep_deficit",
        title=f"Sleep-deficit interaction — muscle loss risk",
        body=" ".join(body_parts),
    )


def assess_taper_readiness(
    weeks_in_deficit: Optional[int],
    weight_current: Optional[float],
    weight_target: Optional[float],
    weekly_loss_rate: Optional[float],
) -> Optional[Insight]:
    """Suggest when to start tapering the deficit (reverse diet)."""
    if not all([weeks_in_deficit, weight_current, weight_target]):
        return None

    remaining = weight_current - weight_target
    if remaining <= 0:
        return Insight(
            severity="positive", category="weight",
            title="Target weight reached — time to reverse diet",
            body=f"You've hit {weight_target} lbs. Start reverse dieting: "
                 f"add 100-150 cal/week for 4-6 weeks. Expect 2-3 lbs of water/glycogen. "
                 f"Strength should start recovering within 2-3 weeks.",
        )

    if remaining <= 3 and weekly_loss_rate and weekly_loss_rate > 0:
        weeks_left = remaining / weekly_loss_rate
        return Insight(
            severity="neutral", category="weight",
            title=f"~{remaining:.1f} lbs to target — begin planning exit",
            body=f"At {weekly_loss_rate:.1f} lbs/week, ~{weeks_left:.0f} weeks remain. "
                 f"Start thinking about reverse diet strategy: calorie targets, "
                 f"training volume adjustments, new maintenance calories.",
        )

    return None


def assess_nutrition_deviation(
    meals_today: Optional[list],
    cal_target: Optional[float] = None,
    bed_time: Optional[str] = None,
    as_of_hour: Optional[int] = None,
) -> list[Insight]:
    """Flag unplanned caloric surpluses and late-night eating.

    Returns a list of 0-2 insights (surplus flag + late eating flag).
    """
    results = []
    if not meals_today:
        return results

    # Calculate today's total calories
    total_cal = 0
    for m in meals_today:
        cal = m.get("calories", "")
        if cal and str(cal).strip():
            try:
                total_cal += float(cal)
            except (ValueError, TypeError):
                pass

    # Surplus flag: >30% over target
    if cal_target and cal_target > 0 and total_cal > cal_target * 1.3:
        surplus = total_cal - cal_target
        pct = ((total_cal / cal_target) - 1) * 100
        results.append(Insight(
            severity="warning",
            category="nutrition",
            title=f"Unplanned surplus: +{surplus:.0f} cal ({pct:.0f}% over target)",
            body=f"Today's intake is {total_cal:.0f} cal vs {cal_target:.0f} target. "
                 f"If this was a planned refeed, no action needed. If unplanned, "
                 f"note what triggered it — sleep debt and stress are common drivers.",
        ))

    # Late-night eating flag
    if as_of_hour is not None:
        cutoff_hour = 21  # 9 PM default
        if bed_time:
            try:
                bh, _bm = bed_time.split(":")
                cutoff_hour = int(bh) - 2  # 2 hours before bed
            except (ValueError, IndexError):
                pass
        evening_meals = [
            m for m in meals_today
            if m.get("time_of_day", "").upper() == "EVE"
        ]
        if evening_meals and as_of_hour >= cutoff_hour:
            results.append(Insight(
                severity="neutral",
                category="nutrition",
                title="Late meal logged — sleep impact",
                body="Eating within 2 hours of bedtime raises core body temperature "
                     "and delays sleep onset. This directly conflicts with the sleep stack protocol.",
            ))

    return results
