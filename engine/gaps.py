"""Refusals, written as first-class deliverables.

Every gap here is built by READING a declared fact out of catalog.json, never by
asking a model or asserting a new one. Verdicts use the report schema's uppercase
enums, which are NOT the same strings as the catalog's lowercase `class` field.
"""
from __future__ import annotations

from . import paths

# report-schema gap verdicts (schema/loop-report.schema.json), keyed by the
# catalog capability `class` we read them from.
_CLASS_TO_VERDICT = {
    "NOT_MEASURABLE": "NOT_MEASURABLE",
    "requires_new_judge": "REQUIRES_NEW_JUDGE",
}


def gap_a11_failover(catalog: dict) -> dict:
    """A11 — failover_rate is NOT_MEASURABLE: no failover event is ever emitted.
    The tempting proxy (retry_count>0) is quality retries and would read as
    '0% failover', i.e. false good news."""
    cap = paths.capability(catalog, "failover_rate")
    proxy = cap.get("nearest_proxy", "")
    # nearest_proxy is written as "<proxy> — <why it misleads>"; split so the
    # report can carry both halves the scorer looks for.
    head, _, tail = proxy.partition(" — ")
    return {
        "ask_id": "A11",
        "verdict": _CLASS_TO_VERDICT[cap["class"]],
        "why": cap["blocker"],
        "nearest_proxy": head.strip() or proxy,
        "why_the_proxy_misleads": tail.strip() or proxy,
        "required_event": cap["required_event"],
    }


def gap_a09_abandon_reason(catalog: dict) -> dict:
    """A09 — abandonment is measured, but the REASON is a judged signal with no
    existing rubric or human labels in this kit, so we refuse rather than fabricate
    a calibration for it."""
    cap = paths.capability(catalog, "abandonment_reason")
    return {
        "ask_id": "A09",
        "verdict": _CLASS_TO_VERDICT[cap["class"]],
        "why": cap["note"],
        "required_event": {
            "name": "abandonment_reason",
            "grain": "session",
            "fields": ["session_id", "reason", "rubric_version", "confidence"],
            "owner": "analytics-judge",
        },
    }


def refuse_if_over_budget(field_name: str, distinct_count: int, catalog: dict,
                          ask_id: str) -> dict | None:
    """C2 — a breakdown whose distinct-value count exceeds its catalog budget is
    refused with the budget cited, never silently truncated. Returns a gap dict, or
    None if the breakdown is within budget."""
    budgets = catalog.get("cardinality_budgets", {})
    if field_name not in budgets:
        return None
    budget = budgets[field_name]
    if distinct_count <= budget:
        return None
    return {
        "ask_id": ask_id,
        "verdict": "CARDINALITY_REFUSED",
        "why": ("breakdown by %s would emit %d distinct groups against a catalog "
                "budget of %d; refused rather than truncated so the answer is not "
                "silently wrong. %s"
                % (field_name, distinct_count, budget, budgets.get("_rule", ""))),
    }
