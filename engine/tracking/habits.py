"""Habit streak tracking and gap analysis."""

from datetime import datetime, timedelta
from typing import Optional


def streak(
    dates: list[str],
    as_of: Optional[str] = None,
) -> int:
    """
    Calculate current streak of consecutive days.

    Args:
        dates: List of ISO date strings (YYYY-MM-DD) when habit was completed
        as_of: Reference date (defaults to today)

    Returns:
        Number of consecutive days ending at as_of
    """
    if not dates:
        return 0

    ref = datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else datetime.now().date()
    date_set = {datetime.strptime(d, "%Y-%m-%d").date() for d in dates}

    # Start from today. If today isn't logged yet, start from yesterday.
    # This prevents the streak from showing 0 before the user checks in today.
    check = ref
    if check not in date_set:
        check = ref - timedelta(days=1)

    count = 0
    while check in date_set:
        count += 1
        check -= timedelta(days=1)

    return count


def gap_analysis(
    dates: list[str],
    window_days: int = 30,
    as_of: Optional[str] = None,
    started_on: Optional[str] = None,
) -> dict:
    """
    Analyze gaps in a habit over a window.

    Args:
        dates: List of ISO date strings when habit was completed
        window_days: How many days back to analyze
        as_of: Reference date (defaults to today)

    Returns:
        Dict with completion_rate, longest_streak, current_streak, gaps (list of gap lengths)
    """
    ref = datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else datetime.now().date()
    # If a start date is provided, use actual habit age instead of fixed window
    if started_on:
        start = datetime.strptime(started_on, "%Y-%m-%d").date()
        window_days = (ref - start).days + 1  # inclusive
        window_days = max(window_days, 1)  # avoid division by zero
    else:
        start = ref - timedelta(days=window_days - 1)
    date_set = {datetime.strptime(d, "%Y-%m-%d").date() for d in dates}

    # Count completions in window
    completions = 0
    gaps = []
    current_gap = 0
    longest_streak = 0
    current_streak_val = 0

    for i in range(window_days):
        day = start + timedelta(days=i)
        if day in date_set:
            completions += 1
            if current_gap > 0:
                gaps.append(current_gap)
                current_gap = 0
            current_streak_val += 1
            longest_streak = max(longest_streak, current_streak_val)
        else:
            current_gap += 1
            current_streak_val = 0

    if current_gap > 0:
        gaps.append(current_gap)

    return {
        "completion_rate": round(completions / window_days * 100, 1),
        "completions": completions,
        "window_days": window_days,
        "longest_streak": longest_streak,
        "current_streak": streak(dates, as_of),
        "gaps": gaps,
        "avg_gap": round(sum(gaps) / len(gaps), 1) if gaps else 0,
    }
