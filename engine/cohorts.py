"""Shared cohort/time primitives for detection. Read-only over in-memory rows."""
from __future__ import annotations

from collections import defaultdict

MIN_COHORT_SESSIONS = 30   # below this a cohort is too noisy to trust (esp. on sample)


def resolution_rate(sessions) -> float:
    if not sessions:
        return 0.0
    return sum(1 for s in sessions if s["session_end"] == "resolved") / len(sessions)


def in_window(rows, lo, hi, day_field="day"):
    """Rows with lo <= day <= hi."""
    return [r for r in rows if lo <= r[day_field] <= hi]


def before_after(rows, change_day, span=14, day_field="day"):
    """Split rows into the `span` days before change_day and the `span` days from
    change_day onward."""
    before = [r for r in rows if change_day - span <= r[day_field] < change_day]
    after = [r for r in rows if change_day <= r[day_field] < change_day + span]
    return before, after


def first_appearance_day(rows, day_field="day") -> int | None:
    days = [r[day_field] for r in rows]
    return min(days) if days else None


def daily_counts(rows, day_field="day") -> dict:
    c = defaultdict(int)
    for r in rows:
        c[r[day_field]] += 1
    return dict(c)


def by_intent(sessions) -> dict:
    out = defaultdict(list)
    for s in sessions:
        out[s["intent"]].append(s)
    return dict(out)


def by_tenant(rows) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r["tenant"]].append(r)
    return dict(out)


def percentile(values, q) -> float:
    """q in [0,1]. Nearest-rank percentile; stdlib only."""
    if not values:
        return 0.0
    vs = sorted(values)
    idx = min(len(vs) - 1, max(0, int(round(q * (len(vs) - 1)))))
    return vs[idx]


def median(values) -> float:
    return percentile(values, 0.5)
