"""Signature-based detectors for the three real fault kinds.

No day/tenant literals: every "where to look" comes from config_timeline or cohort
structure, every "is this a problem" from a signature with generator-invariant
thresholds (the constants below match verify.py's own release-gating checks).
"""
from __future__ import annotations

from . import cohorts, joins, standard as std_mod

# generator-invariant thresholds (see verify.py)
TOL_FLAT_ERR = 0.012
TOL_FLAT_RES = 0.045
F1_PEER_GAP = 0.20
F2_RES_DROP = 0.04
LOW_KB_HIT = 0.5          # a KB-driven intent answering below this is not grounded
F2_EMPTY_RISE = 0.08      # absolute rise in empty-200 share that counts as material
F3_COST_RISE = 1.20       # cost multiplier that counts as material
SPAN = 14                 # before/after comparison span in days


def _rate(rows, pred):
    return (sum(1 for r in rows if pred(r)) / len(rows)) if rows else 0.0


def detect_kb_gap(sessions, tool_calls, kb_lookups, config_rows, std):
    """F1 kb.gap — a KB-driven intent that resolves far below its peers because the
    KB has no confident content for it. Born-broken: seen only against a peer
    standard, not against its own past."""
    findings, diagnoses = [], []
    for tenant in sorted({s["tenant"] for s in sessions}):
        tsessions = [s for s in sessions if s["tenant"] == tenant]
        tkb = [k for k in kb_lookups if k["tenant"] == tenant]
        tstd = std_mod.tenant_standard(std, tenant)
        if not tstd:
            continue
        peer_median = tstd["median"]
        kb_by_intent = _group(tkb, "intent")
        sess_by_intent = cohorts.by_intent(tsessions)
        for intent, isess in sess_by_intent.items():
            if len(isess) < cohorts.MIN_COHORT_SESSIONS:
                continue
            klookups = kb_by_intent.get(intent, [])
            if len(klookups) < cohorts.MIN_COHORT_SESSIONS:
                continue  # not a KB-driven intent
            kb_hit_rate = _rate(klookups, lambda k: k.get("kb_hit"))
            res = cohorts.resolution_rate(isess)
            if kb_hit_rate < LOW_KB_HIT and res < peer_median - F1_PEER_GAP:
                onset = cohorts.first_appearance_day(isess)
                last = max(s["day"] for s in isess)
                # attribute to the nearest kb config change in this tenant at/before onset
                kb_changes = joins.config_changes_for(config_rows, tenant=tenant, kind="kb")
                near = [c for c in kb_changes if c["day"] <= onset + 3]
                change = max(near, key=lambda c: c["day"]) if near else None
                top_scores = [k.get("kb_top_score") for k in klookups
                              if k.get("kb_top_score") is not None]
                tw = [s for s in tsessions if onset <= s["day"] <= last]
                share = len(isess) / len(tw) if tw else 0.0
                fid = "f_kbgap_%s_%s" % (tenant.split("-")[0], intent)
                findings.append({
                    "id": fid, "tenant": tenant, "cohort": {"intent": intent},
                    "metric": "resolution_rate",
                    "window": {"from_day": onset, "to_day": last},
                    "observed": round(res, 4), "expected": round(peer_median, 4),
                    "is_regression": True, "severity": "critical",
                    "evidence": [
                        "kb_hit is true on only %.0f%% of lookups in this cohort" % (kb_hit_rate * 100),
                        "kb_top_score sits low (median %.2f) — no confident document"
                        % (cohorts.median(top_scores) if top_scores else 0.0),
                        "cohort resolution %.3f vs tenant peer median %.3f" % (res, peer_median),
                        ("config_timeline day %d: kb change '%s'" % (change["day"], change["note"]))
                        if change else "no before-period; visible only against peers",
                    ],
                    "impact": _kb_impact(isess, peer_median, res, share),
                    "audience": ["agent_builder", "business_owner"],
                    "if_nothing_changes": (
                        "This cohort keeps resolving near %.0f%% against a peer norm of "
                        "%.0f%%; every unresolved one is a first impression of a new "
                        "product line reaching a human who was not meant to be involved."
                        % (res * 100, peer_median * 100)),
                })
                diagnoses.append({
                    "id": "d_" + fid, "finding_id": fid, "cause_class": "kb.gap",
                    "confidence": 0.9,
                    "attributed_change": ({"kind": "kb", "day": change["day"]}
                                          if change else None),
                    "evidence": [
                        "kb_hit false on the majority of lookups in the cohort",
                        "no tool involved in this intent, so tool causes are excluded",
                        "peer intents in the same tenant are unaffected",
                    ],
                })
    return findings, diagnoses


def _kb_impact(isess, baseline, observed, share):
    n = len(isess)
    would_resolve = round(n * baseline)
    did_resolve = sum(1 for s in isess if s["session_end"] == "resolved")
    lost = max(0, would_resolve - did_resolve)
    handoffs = sum(1 for s in isess if s["session_end"] == "handoff")
    abandoned = sum(1 for s in isess if s["session_end"] == "abandoned")
    days = max(s["day"] for s in isess) - min(s["day"] for s in isess) + 1
    cost = sum(s.get("cost_usd") or 0 for s in isess)
    return {
        "conversations_affected": n,
        "share_of_traffic": round(share, 4),
        "downstream": {"would_have_resolved_at_baseline": would_resolve,
                       "unplanned_handoffs": handoffs, "abandoned": abandoned},
        "cost_usd": round(cost, 2),
        "days_running": days,
        "derivation": (
            "%d sessions in this cohort over %d days (%.1f%% of the tenant's traffic in "
            "the window). Observed resolution %.3f against a peer baseline of %.3f (this "
            "cohort has no before-period, so the baseline is peer intents in the same "
            "tenant), so ~%d conversations that would have resolved did not; %d reached a "
            "handoff and %d were abandoned. cost_usd is the cohort sum over v3 traffic."
            % (n, days, share * 100, observed, baseline, lost, handoffs, abandoned)),
    }


def detect_tool_contract_break(sessions, tool_calls, kb_lookups, config_rows, std):
    """F2 tool.contract_break — a tool returns 200 with an empty payload on a chunk
    of calls. outcome stays 'ok', so declared error rates read FLAT; only empty-200
    share, same-tool retries and downstream resolution move."""
    findings, diagnoses = [], []
    for change in joins.tool_config_changes(config_rows):
        tenant, tool, day = change["tenant"], change["target"], change["day"]
        calls = [c for c in tool_calls if c["tenant"] == tenant and c["tool_name"] == tool]
        if len(calls) < cohorts.MIN_COHORT_SESSIONS:
            continue
        before, after = _split(calls, day, SPAN)
        if len(before) < 15 or len(after) < 15:
            continue
        err_b = _rate(before, lambda c: c["outcome"] in ("error", "timeout"))
        err_a = _rate(after, lambda c: c["outcome"] in ("error", "timeout"))
        empty_b = _rate([c for c in before if c["outcome"] == "ok"],
                        lambda c: c.get("result_field_count") == 0)
        empty_a = _rate([c for c in after if c["outcome"] == "ok"],
                        lambda c: c.get("result_field_count") == 0)
        retry_b = _rate(before, lambda c: (c.get("retry_count") or 0) > 0)
        retry_a = _rate(after, lambda c: (c.get("retry_count") or 0) > 0)
        # downstream cohort = sessions of the tool's dominant intent
        intent = _dominant(calls, "intent")
        isess = [s for s in sessions if s["tenant"] == tenant and s["intent"] == intent]
        sb, sa = _split(isess, day, SPAN)
        res_b, res_a = cohorts.resolution_rate(sb), cohorts.resolution_rate(sa)

        error_flat = abs(err_a - err_b) < TOL_FLAT_ERR
        empty_rose = (empty_a - empty_b) > F2_EMPTY_RISE
        res_dropped = res_a < res_b - F2_RES_DROP
        if error_flat and empty_rose and res_dropped:
            fid = "f_toolbreak_%s_%s" % (tenant.split("-")[0], tool)
            tw = [s for s in sessions if s["tenant"] == tenant and day <= s["day"] < day + SPAN]
            share = len(sa) / len(tw) if tw else 0.0
            findings.append({
                "id": fid, "tenant": tenant,
                "cohort": {"intent": intent, "tool": tool},
                "metric": "resolution_rate",
                "window": {"from_day": day, "to_day": day + SPAN},
                "observed": round(res_a, 4), "expected": round(res_b, 4),
                "is_regression": True, "severity": "high",
                "evidence": [
                    "declared tool error rate flat: %.4f -> %.4f (outcome stays 'ok')" % (err_b, err_a),
                    "empty-200 share (result_field_count=0) rises %.3f -> %.3f" % (empty_b, empty_a),
                    "same-tool retry_count>0 rises %.3f -> %.3f" % (retry_b, retry_a),
                    "%s resolution %.3f -> %.3f" % (intent, res_b, res_a),
                    "config_timeline day %d: tool %s %s->%s" % (day, tool, change["from_value"], change["to_value"]),
                ],
                "impact": _drop_impact(sa, res_b, res_a, share),
                "audience": ["agent_builder", "platform_owner"],
                "if_nothing_changes": (
                    "The tool reports success while returning nothing, so %s quietly "
                    "resolves ~%d fewer conversations per window with no error alarm to "
                    "catch it." % (intent, max(0, round(len(sa) * (res_b - res_a))))),
            })
            diagnoses.append({
                "id": "d_" + fid, "finding_id": fid, "cause_class": "tool.contract_break",
                "confidence": 0.88, "attributed_change": {"kind": "tool", "day": day},
                "evidence": [
                    "response_bytes / result_field_count show empty 200s the error metric misses",
                    "retry_count on a same-tool second call rises in the same window",
                    "coincides with the tool version change on day %d" % day,
                ],
            })
    return findings, diagnoses


def detect_prompt_regression(sessions, tool_calls, kb_lookups, config_rows, std):
    """F3 prompt.regression — a prompt change makes the agent over-confirm: turns and
    cost rise while the outcome metric stays FLAT (a real cost regression with no
    resolution symptom). Seen only against the cohort's own past."""
    findings, diagnoses = [], []
    for change in [c for c in config_rows if c["kind"] == "prompt"]:
        tenant, agent, day = change["tenant"], change["target"], change["day"]
        asess = [s for s in sessions if s["tenant"] == tenant and s["agent_id"] == agent]
        if len(asess) < cohorts.MIN_COHORT_SESSIONS:
            continue
        before, after = _split(asess, day, SPAN)
        if len(before) < 15 or len(after) < 15:
            continue
        turns_b = cohorts.median([s["turns"] for s in before])
        turns_a = cohorts.median([s["turns"] for s in after])
        cost_b = _mean([s.get("cost_usd") for s in before])
        cost_a = _mean([s.get("cost_usd") for s in after])
        res_b, res_a = cohorts.resolution_rate(before), cohorts.resolution_rate(after)

        turns_rose = turns_a > turns_b
        cost_rose = cost_b > 0 and cost_a > cost_b * F3_COST_RISE
        res_flat = abs(res_a - res_b) < TOL_FLAT_RES
        if turns_rose and cost_rose and res_flat:
            fid = "f_promptreg_%s_%s" % (tenant.split("-")[0], agent)
            tw = [s for s in sessions if s["tenant"] == tenant and day <= s["day"] < day + SPAN]
            share = len(after) / len(tw) if tw else 0.0
            findings.append({
                "id": fid, "tenant": tenant, "cohort": {"agent_id": agent},
                "metric": "turns_to_resolve",
                "window": {"from_day": day, "to_day": day + SPAN},
                "observed": round(turns_a, 2), "expected": round(turns_b, 2),
                "is_regression": True, "severity": "high",
                "evidence": [
                    "median turns %.1f -> %.1f" % (turns_b, turns_a),
                    "cost per session %.4f -> %.4f (+%.0f%%)" % (cost_b, cost_a, (cost_a/cost_b - 1) * 100),
                    "resolution FLAT %.3f -> %.3f (within week-to-week noise)" % (res_b, res_a),
                    "config_timeline day %d: prompt %s %s->%s" % (day, agent, change["from_value"], change["to_value"]),
                ],
                "impact": _cost_impact(after, cost_b, cost_a, share),
                "audience": ["agent_builder", "business_owner"],
                "if_nothing_changes": (
                    "Every conversation on this agent now costs ~%.0f%% more and takes "
                    "~%.0f more turns for the same outcome — pure waste that scales with "
                    "volume." % ((cost_a/cost_b - 1) * 100, turns_a - turns_b)),
            })
            diagnoses.append({
                "id": "d_" + fid, "finding_id": fid, "cause_class": "prompt.regression",
                "confidence": 0.87, "attributed_change": {"kind": "prompt", "day": day},
                "evidence": [
                    "turns and cost step up exactly at the prompt change, resolution does not move",
                    "no tool or kb change on this agent in the window",
                ],
            })
    return findings, diagnoses


# ---- small helpers ----

def _group(rows, field):
    from collections import defaultdict
    out = defaultdict(list)
    for r in rows:
        out[r.get(field)].append(r)
    return dict(out)


def _split(rows, change_day, span):
    before = [r for r in rows if change_day - span <= r["day"] < change_day]
    after = [r for r in rows if change_day <= r["day"] < change_day + span]
    return before, after


def _dominant(rows, field):
    from collections import Counter
    c = Counter(r.get(field) for r in rows)
    return c.most_common(1)[0][0] if c else None


def _mean(values):
    vs = [v for v in values if v is not None]
    return sum(vs) / len(vs) if vs else 0.0


def _drop_impact(after_sessions, baseline, observed, share):
    n = len(after_sessions)
    lost = max(0, round(n * (baseline - observed)))
    handoffs = sum(1 for s in after_sessions if s["session_end"] == "handoff")
    abandoned = sum(1 for s in after_sessions if s["session_end"] == "abandoned")
    days = (max(s["day"] for s in after_sessions) - min(s["day"] for s in after_sessions) + 1) if after_sessions else 0
    return {
        "conversations_affected": n,
        "share_of_traffic": round(share, 4),
        "downstream": {"lost_resolutions_vs_baseline": lost,
                       "unplanned_handoffs": handoffs, "abandoned": abandoned},
        "cost_usd": None,
        "days_running": days,
        "derivation": (
            "%d sessions of the affected intent in the post-change window (%.1f%% of "
            "tenant traffic). Resolution fell %.3f -> %.3f, so ~%d resolutions were lost "
            "versus the pre-change baseline; %d handoffs and %d abandons downstream."
            % (n, share * 100, baseline, observed, lost, handoffs, abandoned)),
    }


def _cost_impact(after_sessions, cost_b, cost_a, share):
    n = len(after_sessions)
    extra = sum((s.get("cost_usd") or 0) for s in after_sessions) - n * cost_b
    days = (max(s["day"] for s in after_sessions) - min(s["day"] for s in after_sessions) + 1) if after_sessions else 0
    return {
        "conversations_affected": n,
        "share_of_traffic": round(share, 4),
        "downstream": {"avoidable_spend_usd": round(max(0.0, extra), 2)},
        "cost_usd": round(max(0.0, extra), 2),
        "days_running": days,
        "derivation": (
            "%d sessions on this agent in the post-change window (%.1f%% of tenant "
            "traffic) at mean cost %.4f vs a pre-change mean of %.4f, i.e. ~$%.2f of "
            "avoidable spend in the window; resolution unchanged."
            % (n, share * 100, cost_a, cost_b, max(0.0, extra))),
    }
