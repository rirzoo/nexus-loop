"""The semantic catalog — the artifact that makes planning possible.

A planner over a raw schema hallucinates joins; a planner over a curated
semantic model works. This file is the curated model: what each field MEANS,
which joins are SOUND, what is measurable AT ALL, and over what coverage.
"""
from __future__ import annotations

from typing import Dict

from . import world as W


def build(variant: str) -> Dict:
    acme, nw = W.ACME, W.NORTHWIND
    return {
        "catalog_version": "1.0.0",
        "corpus_variant": variant,
        "note": "Plan over this file, not over the raw column list. Every capability "
                "declares its own coverage; a metric that ignores coverage is wrong "
                "even when its arithmetic is right.",

        "entities": {
            "session": {
                "grain": "one conversation",
                "table": "corpus/sessions.jsonl.gz",
                "key": "session_id",
                "valid_measures": ["count", "resolution_rate", "handoff_rate", "abandon_rate",
                                   "turns", "duration_s", "cost_usd", "quality_score", "csat"],
            },
            "step": {
                "grain": "one execution step inside a turn",
                "table": "corpus/agent_steps.jsonl.gz",
                "key": ["session_id", "step_seq"],
                "valid_measures": ["count", "duration_ms", "error_rate", "cost_usd",
                                   "input_tokens", "output_tokens"],
                "warning": "Steps fan out per session. Averaging a step measure over "
                           "sessions without re-aggregating to session grain first is "
                           "the single most common error against this corpus.",
            },
            "turn": {
                "grain": "one user/agent exchange, with text",
                "table": "corpus/turns.jsonl.gz",
                "key": ["session_id", "turn_index"],
                "valid_measures": ["count"],
                "warning": "Text only. A transcript cannot distinguish a 500 from a "
                           "timeout from a guardrail block from not knowing the answer "
                           "— all four render as the same apology string.",
            },
            "config_change": {
                "grain": "one configuration change",
                "table": "corpus/config_timeline.csv",
                "key": ["day", "tenant", "kind", "target"],
                "use": "change markers for attribution. A quality movement with no "
                       "change marker near it is usually a mix shift, not a regression.",
            },
        },

        "joins": [
            {"from": "step.session_id", "to": "session.session_id",
             "validity": "SOUND", "cardinality": "many-to-one"},
            {"from": "turn.(session_id,turn_index)", "to": "step.(session_id,turn_index)",
             "validity": "SOUND", "cardinality": "one-to-many"},
            {"from": "session.(tenant,day)", "to": "config_change.(tenant,day)",
             "validity": "SOUND_WITH_CARE",
             "note": "config_change.tenant may be '*' (applies to all tenants). Joining "
                     "on equality alone silently drops the judge-version change."},
            {"from": "step.tool_name", "to": "config_change.target",
             "validity": "SOUND_WITH_CARE",
             "note": "only for kind='tool'. Joining without filtering kind fans out."},
            {"from": "session.quality_score", "to": "session.quality_score",
             "validity": "UNSOUND_ACROSS_JUDGE_VERSIONS",
             "note": "quality_score is only comparable within one judge_version. "
                     "Trending it across a version boundary measures the rubric."},
        ],

        "fields": {
            "step.step_type": {"kind": "dimension", "closed_set": W.STEP_TYPES},
            "step.outcome": {"kind": "dimension", "closed_set": W.OUTCOMES,
                             "semantics": "the outcome the RUNTIME observed. 'ok' means the "
                                          "call returned without an error — it does NOT mean "
                                          "the call returned anything useful."},
            "step.error_class": {"kind": "dimension", "closed_set": W.ERROR_CLASSES,
                                 "nullable": True,
                                 "semantics": "low-cardinality taxonomy. Null when outcome='ok'."},
            "step.status_code": {"kind": "dimension",
                                 "semantics": "HTTP status for tool_call. 200 with an empty "
                                              "body is a successful call and a failed answer."},
            "step.response_bytes": {"kind": "measure",
                                    "semantics": "payload size of a tool response. The only "
                                                 "field that distinguishes a useful 200 from "
                                                 "an empty one."},
            "step.result_field_count": {"kind": "measure",
                                        "semantics": "fields parsed out of a tool response. 0 "
                                                     "on a 200 means the agent got nothing to "
                                                     "answer with."},
            "step.retry_count": {"kind": "measure",
                                 "semantics": "attempt index. >0 means this step is a retry of "
                                              "the previous one."},
            "step.kb_hit": {"kind": "dimension", "semantics": "did retrieval return a "
                                                              "confident document"},
            "step.kb_top_score": {"kind": "measure", "semantics": "retrieval confidence 0-1"},
            "step.milestone": {"kind": "dimension", "nullable": True,
                               "semantics": "the business milestone reached at this step. "
                                            "The unit journeys, funnels and drop-off are all "
                                            "built from."},
            "session.session_end": {"kind": "dimension", "closed_set": W.SESSION_ENDS},
            "session.handoff_by_design": {"kind": "dimension", "nullable": True,
                                          "semantics": "TRUE when the agent was BUILT to hand "
                                                       "off here. Containment is a filter over "
                                                       "this flag, not a philosophical argument."},
            "session.agent_kind": {"kind": "dimension", "closed_set": ["v3_agent", "v2_flow"],
                                   "semantics": "v2_flow sessions emit NO tool_call, kb_lookup "
                                                "or guardrail steps. This is the coverage hole."},
            "session.quality_score": {"kind": "measure", "fidelity": "judged",
                                      "requires": "judge_version",
                                      "semantics": "1-5 rubric score. Comparable only within "
                                                   "one judge_version."},
            "session.judge_version": {"kind": "dimension", "closed_set": ["v1", "v2"]},
            "session.csat": {"kind": "measure", "fidelity": "measured", "nullable": True,
                             "semantics": "user-reported, present on ~8.6% of sessions. "
                                          "Not missing at random."},
            "session.custom_dims.customer_ref": {
                "kind": "dimension", "cardinality_budget": 200,
                "semantics": "near-unique per session. NOT a valid breakdown — the budget "
                             "exists so a planner refuses it rather than melting the store."},
            "session.custom_dims.segment": {"kind": "dimension",
                                            "closed_set": ["mass", "affluent", "priority"],
                                            "cardinality_budget": 50},
        },

        "capabilities": [
            {"id": "tool_failure_rate", "fidelity": "measured", "grain": "step",
             "class": "canonical",
             "coverage": {acme.key: round(1 - acme.v2_share, 3),
                          nw.key: round(1 - nw.v2_share, 3)},
             "coverage_basis": "v2_flow sessions emit no tool_call rows; they must be "
                               "excluded from the denominator, not counted as zero-error."},
            {"id": "containment_rate", "fidelity": "measured", "grain": "session",
             "class": "canonical", "coverage": {acme.key: 1.0, nw.key: 1.0},
             "definition": "sessions ending 'resolved', plus handoffs with "
                           "handoff_by_design = true, over all sessions"},
            {"id": "resolution_rate", "fidelity": "measured", "grain": "session",
             "class": "canonical", "coverage": {acme.key: 1.0, nw.key: 1.0}},
            {"id": "turns_to_resolve", "fidelity": "measured", "grain": "session",
             "class": "canonical", "coverage": {acme.key: 1.0, nw.key: 1.0}},
            {"id": "cost_per_session", "fidelity": "measured", "grain": "session",
             "class": "canonical", "coverage": {acme.key: round(1 - acme.v2_share, 3),
                                                nw.key: round(1 - nw.v2_share, 3)},
             "coverage_basis": "cost is only emitted on llm_call steps, which v2_flow lacks"},
            {"id": "kb_fallthrough_rate", "fidelity": "measured", "grain": "step",
             "class": "canonical",
             "coverage": {acme.key: round(1 - acme.v2_share, 3),
                          nw.key: round(1 - nw.v2_share, 3)}},
            {"id": "quality_score", "fidelity": "judged", "grain": "session",
             "class": "canonical", "judge_versions": ["v1", "v2"],
             "coverage": {acme.key: 1.0, nw.key: 1.0},
             "calibration_available": True,
             "calibration_basis": "labels/rubric_scores.jsonl — 100 human-scored sessions"},
            {"id": "milestone_reached_rate", "fidelity": "measured", "grain": "session",
             "class": "canonical", "coverage": {acme.key: 1.0, nw.key: 1.0}},
            {"id": "silent_tool_success", "fidelity": "derived", "grain": "step",
             "class": "derivable_not_declared",
             "note": "NOT a shipped capability. It is derivable from response_bytes / "
                     "result_field_count on a 200. If your system needs it, it must "
                     "derive and declare it — that is a legitimate answer, not a gap."},
            {"id": "failover_rate", "fidelity": None, "grain": None,
             "class": "NOT_MEASURABLE",
             "blocker": "no failover mechanism exists in the runtime, so no failover event "
                        "is ever emitted. There is no primary/secondary path to observe.",
             "nearest_proxy": "llm_call.retry_count > 0 — but those are QUALITY retries at a "
                              "different temperature, not failovers. Reporting them as "
                              "failover would under-report and read as '0% failover', i.e. "
                              "as good news.",
             "required_event": {"name": "failover", "grain": "step",
                                "fields": ["from_target", "to_target", "reason", "recovered"],
                                "owner": "conversation-runtime"}},
            {"id": "abandonment_reason", "fidelity": "judged", "grain": "session",
             "class": "requires_new_judge",
             "note": "session_end='abandoned' is MEASURED. WHY the user abandoned is a "
                     "judgment about meaning and needs a versioned judged signal with a "
                     "published calibration. Inferring it from the end code is a Rule 1 "
                     "violation dressed up as an aggregate."},
        ],

        "cardinality_budgets": {
            "session.custom_dims.customer_ref": 200,
            "session.session_id": 0,
            "step.step_seq": 0,
            "_rule": "a breakdown whose distinct-value count exceeds its budget must be "
                     "refused with the budget cited, not silently truncated.",
        },

        "milestones": {
            t.key: {i.key: i.milestones for i in
                    (t.intents + ([W.ACME_PREMIUM_INTENT] if t.key == "acme-bank" else []))}
            for t in W.TENANTS
        },

        "tenants": {
            t.key: {"label": t.label, "vertical": t.vertical,
                    "agents": t.agents, "tools": sorted(t.tools.keys()),
                    "kb_docs": t.kb_docs,
                    "v2_flow_share": t.v2_share}
            for t in W.TENANTS
        },
    }
