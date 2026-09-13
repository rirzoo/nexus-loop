"""Coverage computed from data, and the cardinality-budget refusal.

Coverage is a property of a scope, not a metric name. Tool- and cost-derived
metrics exclude v2_flow traffic (which emits no tool_call/llm_call rows) from the
denominator rather than counting it as zero. The 0.72/0.93 figures are NEVER
hardcoded — they are recomputed here so the sealed corpus reports its own truth.
"""
from __future__ import annotations

from collections import defaultdict


def v3_share_by_tenant(sessions) -> dict:
    """Share of each tenant's sessions that are v3_agent — the coverage of any
    tool/kb/cost metric, since v2_flow emits none of those steps."""
    total = defaultdict(int)
    v3 = defaultdict(int)
    for s in sessions:
        total[s["tenant"]] += 1
        if s["agent_kind"] == "v3_agent":
            v3[s["tenant"]] += 1
    return {t: (v3[t] / total[t] if total[t] else 0.0) for t in total}


def distinct_count(sessions, value_fn) -> int:
    seen = set()
    for s in sessions:
        v = value_fn(s)
        if v is not None:
            seen.add(v)
    return len(seen)
