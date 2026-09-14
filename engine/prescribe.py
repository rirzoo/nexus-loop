"""Turn each diagnosed regression into a proposed fix — deterministically.

A prescription here is not a guess from a model: it maps the *classified cause* to the
change class that addresses it, reuses the numbers detection already measured as the
`predicted_delta`, and frames the decision a human is being asked to approve. No LLM, no
network — the same arithmetic-over-logs discipline as detection (Rule 1), so it runs
untouched on the sealed corpus.

Rules of the mapping:
  * A decoy (`is_regression: False`) gets NO prescription — there is nothing to fix.
  * An `unexplained` regression gets `no_action` — investigate, never a fabricated fix.
  * `change_type` is the DIRECT fix class for the cause; the replay endpoint only moves a
    metric when the change matches the fault's accepted class, so the prediction is
    honest only if the mapping is right.
  * `predicted_delta.metric` uses the replay endpoint's own metric name (`median_turns`
    for prompt faults, else `resolution_rate`) so a later verification lines up field-for-
    field; `from` = the observed regressed value, `to` = the baseline, both already
    computed in detection.
"""
from __future__ import annotations

# cause_class -> (change_type, replay_metric). change_type is the direct fix class.
_FIX = {
    "kb.gap":              ("kb.add",       "resolution_rate"),
    "tool.contract_break": ("tool.validate", "resolution_rate"),
    "prompt.regression":   ("prompt.edit",  "median_turns"),
    "unexplained":         ("no_action",    "resolution_rate"),
}


def _target(finding):
    """The concrete thing a change would touch, taken from the cohort key."""
    c = finding.get("cohort") or {}
    if c.get("tool"):
        return c["tool"]
    if c.get("agent_id"):
        return c["agent_id"]
    if c.get("intent"):
        return "%s/%s knowledge base" % (finding["tenant"], c["intent"])
    return finding["tenant"]


def _decision(cause_class, finding, target):
    """What a human is actually being asked to approve, and the honest case against it."""
    c = finding.get("cohort") or {}
    label = c.get("intent") or c.get("agent_id") or c.get("tool") or finding["tenant"]
    if cause_class == "kb.gap":
        return {
            "asking_approval_for":
                "Add the missing knowledge-base content for %s so this cohort can answer "
                "instead of falling through to a human." % label,
            "risk_if_diagnosis_wrong":
                "If the low resolution reflects genuinely hard questions rather than a KB "
                "gap, adding articles will not lift resolution and the effort is wasted.",
            "would_not_ship_if":
                "kb_hit does not rise after the content is added in replay, or a golden "
                "set of already-good conversations regresses.",
        }
    if cause_class == "tool.contract_break":
        return {
            "asking_approval_for":
                "Validate the %s response (reject empty 200s, retry or fall back) so "
                "silent empty successes stop being treated as answered." % target,
            "risk_if_diagnosis_wrong":
                "If those empty responses are legitimately empty (no data for the query), "
                "validation only adds retries and latency without lifting resolution.",
            "would_not_ship_if":
                "resolution does not recover in replay, or the golden set regresses from "
                "the added retries.",
        }
    if cause_class == "prompt.regression":
        return {
            "asking_approval_for":
                "Edit or revert the %s prompt to stop the over-confirmation so turns and "
                "cost fall back to the prior level at the same resolution." % target,
            "risk_if_diagnosis_wrong":
                "If the extra turns reflect genuinely harder traffic, trimming the prompt "
                "could cut resolution to save cost.",
            "would_not_ship_if":
                "median turns does not fall in replay, or resolution drops when the "
                "prompt is trimmed.",
        }
    # unexplained
    return {
        "asking_approval_for":
            "Investigate the unexplained resolution drop in %s before any change is "
            "proposed — the cause is not yet identified." % label,
        "risk_if_diagnosis_wrong":
            "Acting on an unclassified drop risks fixing the wrong thing.",
        "would_not_ship_if":
            "no change is proposed; this asks a human to investigate, not to ship.",
    }


def prescribe(findings, diagnoses):
    """Emit one linked prescription per real, classified regression. Decoys are skipped."""
    dmap = {d["finding_id"]: d for d in diagnoses}
    out = []
    for f in findings:
        if not f.get("is_regression"):
            continue
        d = dmap.get(f["id"])
        if not d:
            continue
        cause = d["cause_class"]
        change_type, metric = _FIX.get(cause, ("no_action", "resolution_rate"))
        target = _target(f)
        no_fix = change_type == "no_action"
        # predicted_delta reuses detection's own numbers; for prompt faults the endpoint's
        # metric is median_turns (observed high -> baseline low), else resolution_rate
        # (observed low -> baseline high). no_action predicts no movement.
        frm = float(f["observed"])
        to = frm if no_fix else float(f["expected"])
        pid = "p_" + f["id"]
        out.append({
            "id": pid,
            "diagnosis_id": d["id"],
            "change_type": change_type,
            "target": target,
            "description": (
                "Direct fix for %s in %s: %s." % (cause, f["tenant"], change_type)
                if not no_fix else
                "No fix proposed for an unclassified regression — investigate first."),
            "autonomy_rung": "L0" if no_fix else "L1",
            "predicted_delta": {"metric": metric, "from": round(frm, 4), "to": round(to, 4)},
            "decision": _decision(cause, f, target),
        })
    return out
