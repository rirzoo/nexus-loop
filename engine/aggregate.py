"""Grain-safe aggregation.

The single most common error against this corpus is averaging a step-grain measure
across sessions without first collapsing to one value per session. These helpers
make the correct path the easy path:

  - rate_over_steps: a step-grain rate (correct when the capability's declared grain
    IS step, e.g. tool_failure_rate = error steps / all steps).
  - reaggregate_to_session: collapse steps to one value per session FIRST, then let
    the caller average across sessions.
"""
from __future__ import annotations

from collections import defaultdict


def rate_over_steps(steps, numerator_pred, denominator_pred=None) -> tuple[float, int, int]:
    """Step-grain rate. Returns (rate, num, den). den defaults to all steps passed."""
    num = den = 0
    for s in steps:
        if denominator_pred is not None and not denominator_pred(s):
            continue
        den += 1
        if numerator_pred(s):
            num += 1
    return (num / den if den else 0.0), num, den


def reaggregate_to_session(steps, reducer, session_id_field="session_id") -> dict:
    """Group steps by session, then reduce each session's step list to one value.
    Returns {session_id: reduced_value}. The caller averages over sessions after
    this, never over raw steps."""
    by_session = defaultdict(list)
    for s in steps:
        by_session[s[session_id_field]].append(s)
    return {sid: reducer(rows) for sid, rows in by_session.items()}


def mean(values) -> float:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def group_by(rows, key_fn) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[key_fn(r)].append(r)
    return dict(out)


def session_rate(sessions, pred) -> tuple[float, int, int]:
    """Share of sessions satisfying pred. Sessions are already session-grain."""
    num = sum(1 for s in sessions if pred(s))
    den = len(sessions)
    return (num / den if den else 0.0), num, den
