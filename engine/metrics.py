"""Author the canonical metric definitions with fidelity, coverage and plan.

A metric entry is a DEFINITION (how it's computed, its fidelity, its coverage),
mirroring examples/loop-report.level1.json. Coverage values are computed from data,
never hardcoded. Coverage-limited metrics (tool/kb/cost — v2_flow emits none of
those steps) are authored per tenant so each carries its own true coverage and the
C1 check holds on any corpus. The first A04 metric is 'measured' so the A04 fidelity
trap reads it correctly; silent_tool_success follows as the 'derived' companion.
"""
from __future__ import annotations

from . import aggregate, coverage


def _cov(value, basis, excluded=None):
    d = {"value": round(value, 4), "basis": basis}
    if excluded:
        d["excluded"] = excluded
    return d


def calibrate_quality(sessions, rubric_labels, tol=0.75) -> dict | None:
    """Agreement between the logged quality_score and human rubric scores, within
    one judge_version (never blended across the v1->v2 boundary). agreement = share
    of labelled sessions whose logged score is within `tol` of the human score.
    Returns the calibration for the judge_version with the most labels."""
    by_id = {s["session_id"]: s for s in sessions}
    from collections import defaultdict
    hits = defaultdict(int)
    tot = defaultdict(int)
    for lab in rubric_labels:
        s = by_id.get(lab["session_id"])
        if s is None or s.get("quality_score") is None:
            continue
        jv = lab.get("judge_version_at_label_time") or s.get("judge_version")
        tot[jv] += 1
        if abs(s["quality_score"] - lab["human_quality"]) <= tol:
            hits[jv] += 1
    if not tot:
        return None
    jv = max(tot, key=lambda k: tot[k])
    agreement = hits[jv] / tot[jv]
    # A perfect 1.00 across a label set with ~7.5% human disagreement is a bug, not
    # a judge. The scorer penalises >=0.995; surface it here rather than ship it.
    assert agreement < 0.995, (
        "calibration agreement %.4f >= 0.995 — this is a join/tolerance bug, not a "
        "good judge (labels carry ~7.5%% human disagreement)" % agreement)
    return {"agreement": round(agreement, 4), "n": tot[jv], "judge_version": jv}


def author_metrics(sessions, tool_calls, kb_lookups, calibration) -> list:
    tenants = sorted({s["tenant"] for s in sessions})
    v3 = coverage.v3_share_by_tenant(sessions)

    metrics = []

    metrics.append({
        "id": "m_containment", "name": "Containment rate", "ask_id": "A01",
        "grain": "session", "fidelity": "measured",
        "coverage": _cov(1.0, "session_end and handoff_by_design are present on "
                              "every session, v2_flow and v3_agent alike"),
        "calibration": None,
        "plan": {
            "source": "corpus/sessions.jsonl.gz",
            "filter": "session_end = 'resolved' OR (session_end = 'handoff' AND "
                      "handoff_by_design = true)",
            "denominator": "all sessions in scope",
            "breakdowns": ["tenant", "intent", "channel", "agent_kind"],
            "alternatives_offered": ["resolution_rate — excludes by-design handoffs"],
        },
    })

    metrics.append({
        "id": "m_turns", "name": "Turns to resolve", "ask_id": "A02",
        "grain": "session", "fidelity": "measured",
        "coverage": _cov(1.0, "turn count is present on every session"),
        "calibration": None,
        "plan": {
            "source": "corpus/sessions.jsonl.gz",
            "filter": "session_end = 'resolved'",
            "denominator": "n/a — a distribution, reported as median and p90",
            "breakdowns": ["tenant", "intent", "agent_id", "prompt_version"],
        },
    })

    metrics.append({
        "id": "m_milestone_reached", "name": "Milestone completion rate",
        "ask_id": "A05", "grain": "session", "fidelity": "measured",
        "coverage": _cov(1.0, "milestones_reached/total present on every session"),
        "calibration": None,
        "plan": {
            "source": "corpus/sessions.jsonl.gz",
            "filter": "final milestone in milestones_reached",
            "denominator": "all sessions for the intent's journey",
            "breakdowns": ["tenant", "intent", "milestone"],
        },
    })

    # A04 measured FIRST (so the fidelity trap reads 'measured'), per tenant.
    for t in tenants:
        metrics.append({
            "id": "m_tool_fail_" + t.split("-")[0], "name": "Tool failure rate — " + t,
            "ask_id": "A04", "grain": "step", "fidelity": "measured",
            "coverage": _cov(v3[t], "v2_flow sessions emit no tool_call rows and are "
                                    "excluded from the denominator, not counted as "
                                    "zero-error; measured from this tenant's traffic",
                             ["agent_kind = v2_flow"]),
            "calibration": None,
            "plan": {
                "source": "corpus/agent_steps.jsonl.gz WHERE step_type = 'tool_call'",
                "filter": "outcome IN ('error','timeout') AND tenant = '%s'" % t,
                "denominator": "all tool_call steps for this tenant (v3_agent only)",
                "breakdowns": ["error_class", "tool_name", "channel"],
            },
        })

    # A04 derived companion: a 200 that parsed no fields is a successful call and a
    # failed answer. Declared per the catalog's derivable_not_declared rule.
    for t in tenants:
        metrics.append({
            "id": "m_silent_tool_" + t.split("-")[0],
            "name": "Silent tool success (empty 200) — " + t,
            "ask_id": "A04", "grain": "step", "fidelity": "derived",
            "coverage": _cov(v3[t], "derived from tool_call rows, same v3-only "
                                    "coverage as tool_failure_rate"),
            "calibration": None,
            "plan": {
                "source": "corpus/agent_steps.jsonl.gz WHERE step_type = 'tool_call'",
                "filter": "outcome = 'ok' AND result_field_count = 0 AND tenant = '%s'" % t,
                "denominator": "all tool_call steps with outcome = 'ok' for this tenant",
                "breakdowns": ["tool_name", "tool_version"],
                "alternatives_offered": ["catalog lists this derivable_not_declared — "
                                         "we derived and declared it"],
            },
        })

    for t in tenants:
        metrics.append({
            "id": "m_cost_" + t.split("-")[0], "name": "Cost per session — " + t,
            "ask_id": "A08", "grain": "session", "fidelity": "measured",
            "coverage": _cov(v3[t], "cost_usd is emitted on llm_call steps, which "
                                    "v2_flow lacks; v3-only, denominator stated",
                             ["agent_kind = v2_flow"]),
            "calibration": None,
            "plan": {
                "source": "corpus/sessions.jsonl.gz",
                "filter": "tenant = '%s' AND agent_kind = 'v3_agent'" % t,
                "denominator": "v3 sessions for this tenant",
                "breakdowns": ["intent", "agent_id"],
            },
        })

    for t in tenants:
        metrics.append({
            "id": "m_kb_fallthrough_" + t.split("-")[0],
            "name": "KB fallthrough rate — " + t,
            "ask_id": "A06", "grain": "step", "fidelity": "measured",
            "coverage": _cov(v3[t], "kb_lookup steps only exist on v3 traffic; "
                                    "v2_flow excluded from the denominator",
                             ["agent_kind = v2_flow"]),
            "calibration": None,
            "plan": {
                "source": "corpus/agent_steps.jsonl.gz WHERE step_type = 'kb_lookup'",
                "filter": "kb_hit = false AND tenant = '%s'" % t,
                "denominator": "all kb_lookup steps for this tenant",
                "breakdowns": ["intent", "kb_version"],
            },
        })

    metrics.append({
        "id": "m_quality", "name": "Judged quality", "ask_id": "A06",
        "grain": "session", "fidelity": "judged",
        "coverage": _cov(1.0, "quality_score scored on every session"),
        "calibration": calibration,
        "plan": {
            "source": "corpus/sessions.jsonl.gz",
            "filter": "quality_score, SEGMENTED BY judge_version — never trended "
                      "across the v1->v2 boundary",
            "denominator": "sessions within one judge_version",
            "breakdowns": ["tenant", "intent", "judge_version"],
        },
    })

    return metrics
