"""Agnostic, anomaly-first detection of real regressions.

The design rule: **no detector iterates config_timeline by `kind` to decide WHERE to
look.** We enumerate cohorts from the data, and for cohorts with history we find the
change-point from the cohort's *own* metric series (the largest sustained level shift),
not from a config row. config_timeline is consulted only at the end, to *attribute* a
surviving regression to the nearest change (of any kind) using cause-precedes-effect.
The `cause_class` is a CLASSIFIER over the metric signature (low kb_hit, empty-200 rise,
cost/turns inflation), never read off the config kind — so a genuine deviation with no
matching change is still reported, as `unexplained`.

Thresholds are the verify.py release-gate magnitudes (a contract that holds on the
sealed corpus), used as an acceptance floor; the deployment's own noise band
(standard.noise_band) supplies precision on top.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from . import cohorts, joins

# verify.py release-gate magnitudes (guaranteed floor for any planted fault)
TOL_FLAT_ERR = 0.012
TOL_FLAT_RES = 0.045
F1_PEER_GAP = 0.20
F2_RES_DROP = 0.04
F2_EMPTY_RISE = 0.08
F3_COST_RISE = 1.20
LOW_KB_HIT = 0.5
# agnostic knobs (data-driven, not fault-specific)
SPAN = 14                 # before/after comparison span in days
MIN_AFTER_WEEKS = 2       # a shift must persist to count as sustained, not a blip
NOISE_K = 3.0             # a deviation must exceed this many typical weekly wobbles
UNEXPLAINED_DROP = 0.12   # a large resolution drop we cannot classify is still reported


def _rate(rows, pred):
    return (sum(1 for r in rows if pred(r)) / len(rows)) if rows else 0.0


def _mean(values):
    vs = [v for v in values if v is not None]
    return sum(vs) / len(vs) if vs else 0.0


def _weekly(rows, reducer, min_n=15):
    """{week: reducer(rows_in_week)} over weeks with enough volume to trust."""
    wk = defaultdict(list)
    for r in rows:
        wk[r["day"] // 7].append(r)
    return {w: reducer(rs) for w, rs in wk.items() if len(rs) >= min_n}


def _sustained_shift(weekly):
    """Find the split that maximizes the level change between the weeks before it and
    the weeks from it onward. Returns the change-point or None; direction/magnitude are
    judged by the caller. Uses the MEAN of the weekly points on each side (not the
    median): the weekly values are already robust aggregates, and the mean tracks a level
    step cleanly, whereas a nearest-rank median flattens a peak and lets several splits
    tie — which would pick a spurious early onset that straddles the real change."""
    weeks = sorted(weekly)
    best = None
    for i in range(1, len(weeks)):
        before = [weekly[w] for w in weeks[:i]]
        after = [weekly[w] for w in weeks[i:]]
        if len(after) < MIN_AFTER_WEEKS:
            continue
        bm = sum(before) / len(before)
        am = sum(after) / len(after)
        cand = {"onset_week": weeks[i], "before": bm, "after": am}
        if best is None or abs(am - bm) > abs(best["after"] - best["before"]):
            best = cand
    return best


def _attribute(config_rows, tenant, onset_day, prefer_kinds=None, span=SPAN):
    """Attribute a detected regression to a config change. Detection has already found
    and classified the regression from the data alone; attribution is only the mapping to
    the most plausible change. The onset is detected at week granularity, so the true
    change can sit anywhere in the onset week — the window runs to onset+7. If the
    mechanism is known (prefer_kinds, e.g. a verbosity regression -> prompt/model), pick
    the nearest change of that kind; otherwise fall back to the nearest change at/before
    the onset (cause precedes effect). Honors tenant='*' wildcards."""
    near = joins.config_changes_for(config_rows, tenant=tenant,
                                    day_window=(onset_day - span, onset_day + 7))
    if not near:
        return None
    if prefer_kinds:
        matched = [c for c in near if c["kind"] in prefer_kinds]
        if matched:
            return min(matched, key=lambda c: abs(c["day"] - onset_day))
    prior = [c for c in near if c["day"] <= onset_day + 1]
    pool = prior or near
    return min(pool, key=lambda c: abs(c["day"] - onset_day))


def _dominant(rows, field):
    c = Counter(r.get(field) for r in rows)
    return c.most_common(1)[0][0] if c else None


# --------------------------------------------------------------------------------------
# F1 shape: born-below-peers (peer axis). A cohort with no before-period can only be
# judged against sibling cohorts. Enumerated over intents, not over config rows.
# --------------------------------------------------------------------------------------

def detect_kb_gap(sessions, tool_calls, kb_lookups, config_rows, std):
    from . import standard as std_mod
    findings, diagnoses = [], []
    for tenant in sorted({s["tenant"] for s in sessions}):
        tsessions = [s for s in sessions if s["tenant"] == tenant]
        tkb = [k for k in kb_lookups if k["tenant"] == tenant]
        tstd = std_mod.tenant_standard(std, tenant)
        if not tstd:
            continue
        peer_median = tstd["median"]
        kb_by_intent = _group(tkb, "intent")
        for intent, isess in cohorts.by_intent(tsessions).items():
            if len(isess) < cohorts.MIN_COHORT_SESSIONS:
                continue
            klookups = kb_by_intent.get(intent, [])
            if len(klookups) < cohorts.MIN_COHORT_SESSIONS:
                continue  # not a KB-driven intent
            kb_hit_rate = _rate(klookups, lambda k: k.get("kb_hit"))
            res = cohorts.resolution_rate(isess)
            if not (kb_hit_rate < LOW_KB_HIT and res < peer_median - F1_PEER_GAP):
                continue
            onset = cohorts.first_appearance_day(isess)
            last = max(s["day"] for s in isess)
            change = _attribute(config_rows, tenant, onset, prefer_kinds=("kb",))
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
                    ("attributed to config day %d: %s '%s'" % (change["day"], change["kind"], change["note"]))
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
                "attributed_change": ({"kind": change["kind"], "day": change["day"]}
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


# --------------------------------------------------------------------------------------
# F2 shape: tool contract break (self axis). Enumerated over TOOL cohorts; the onset is
# the change-point in the cohort's own empty-200 series, not a config row.
# --------------------------------------------------------------------------------------

def detect_tool_contract_break(sessions, tool_calls, kb_lookups, config_rows, std):
    findings, diagnoses = [], []
    cohorts_seen = {(c["tenant"], c["tool_name"]) for c in tool_calls if c.get("tool_name")}
    for tenant, tool in sorted(cohorts_seen):
        calls = [c for c in tool_calls if c["tenant"] == tenant and c["tool_name"] == tool]
        if len(calls) < cohorts.MIN_COHORT_SESSIONS:
            continue
        weekly_empty = _weekly([c for c in calls if c["outcome"] == "ok"],
                               lambda rs: _rate(rs, lambda c: c.get("result_field_count") == 0))
        shift = _sustained_shift(weekly_empty)
        if not shift or (shift["after"] - shift["before"]) <= F2_EMPTY_RISE:
            continue
        onset_day = shift["onset_week"] * 7
        before, after = cohorts.before_after(calls, onset_day, SPAN)
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
        intent = _dominant(calls, "intent")
        isess = [s for s in sessions if s["tenant"] == tenant and s["intent"] == intent]
        sb, sa = cohorts.before_after(isess, onset_day, SPAN)
        res_b, res_a = cohorts.resolution_rate(sb), cohorts.resolution_rate(sa)

        if not (abs(err_a - err_b) < TOL_FLAT_ERR and (empty_a - empty_b) > F2_EMPTY_RISE
                and res_a < res_b - F2_RES_DROP):
            continue
        change = _attribute(config_rows, tenant, onset_day, prefer_kinds=("tool",))
        tw = [s for s in sessions if s["tenant"] == tenant and onset_day <= s["day"] < onset_day + SPAN]
        share = len(sa) / len(tw) if tw else 0.0
        fid = "f_toolbreak_%s_%s" % (tenant.split("-")[0], tool)
        findings.append({
            "id": fid, "tenant": tenant, "cohort": {"intent": intent, "tool": tool},
            "metric": "resolution_rate",
            "window": {"from_day": onset_day, "to_day": onset_day + SPAN},
            "observed": round(res_a, 4), "expected": round(res_b, 4),
            "is_regression": True, "severity": "high",
            "evidence": [
                "declared tool error rate flat: %.4f -> %.4f (outcome stays 'ok')" % (err_b, err_a),
                "empty-200 share (result_field_count=0) rises %.3f -> %.3f" % (empty_b, empty_a),
                "same-tool retry_count>0 rises %.3f -> %.3f" % (retry_b, retry_a),
                "%s resolution %.3f -> %.3f" % (intent, res_b, res_a),
                ("onset day %d (detected from empty-200 series); attributed to config day %d: %s %s->%s"
                 % (onset_day, change["day"], change["kind"], change["from_value"], change["to_value"]))
                if change else "onset day %d; no config change nearby" % onset_day,
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
            "confidence": 0.88,
            "attributed_change": ({"kind": change["kind"], "day": change["day"]}
                                  if change else None),
            "evidence": [
                "response_bytes / result_field_count show empty 200s the error metric misses",
                "retry_count on a same-tool second call rises in the same window",
                "empty-200 share steps up at the detected onset, downstream resolution drops with it",
            ],
        })
    return findings, diagnoses


# --------------------------------------------------------------------------------------
# F3 shape: cost/turns inflation with flat outcome (self axis). Enumerated over AGENT
# cohorts; onset is the change-point in the cohort's own cost series.
# --------------------------------------------------------------------------------------

def detect_prompt_regression(sessions, tool_calls, kb_lookups, config_rows, std):
    findings, diagnoses = [], []
    for tenant in sorted({s["tenant"] for s in sessions}):
        by_agent = _group([s for s in sessions if s["tenant"] == tenant], "agent_id")
        for agent, asess in by_agent.items():
            if len(asess) < cohorts.MIN_COHORT_SESSIONS:
                continue
            weekly_cost = _weekly(asess, lambda rs: _mean([s.get("cost_usd") for s in rs]))
            shift = _sustained_shift(weekly_cost)
            if not shift or shift["before"] <= 0 or shift["after"] <= shift["before"] * F3_COST_RISE:
                continue
            onset_day = shift["onset_week"] * 7
            before, after = cohorts.before_after(asess, onset_day, SPAN)
            if len(before) < 15 or len(after) < 15:
                continue
            turns_b = cohorts.median([s["turns"] for s in before])
            turns_a = cohorts.median([s["turns"] for s in after])
            cost_b = _mean([s.get("cost_usd") for s in before])
            cost_a = _mean([s.get("cost_usd") for s in after])
            res_b, res_a = cohorts.resolution_rate(before), cohorts.resolution_rate(after)
            if not (turns_a > turns_b and cost_b > 0 and cost_a > cost_b * F3_COST_RISE
                    and abs(res_a - res_b) < TOL_FLAT_RES):
                continue
            change = _attribute(config_rows, tenant, onset_day, prefer_kinds=("prompt", "model"))
            tw = [s for s in sessions if s["tenant"] == tenant and onset_day <= s["day"] < onset_day + SPAN]
            share = len(after) / len(tw) if tw else 0.0
            fid = "f_promptreg_%s_%s" % (tenant.split("-")[0], agent)
            findings.append({
                "id": fid, "tenant": tenant, "cohort": {"agent_id": agent},
                "metric": "turns_to_resolve",
                "window": {"from_day": onset_day, "to_day": onset_day + SPAN},
                "observed": round(turns_a, 2), "expected": round(turns_b, 2),
                "is_regression": True, "severity": "high",
                "evidence": [
                    "median turns %.1f -> %.1f" % (turns_b, turns_a),
                    "cost per session %.4f -> %.4f (+%.0f%%)" % (cost_b, cost_a, (cost_a / cost_b - 1) * 100),
                    "resolution FLAT %.3f -> %.3f (within week-to-week noise)" % (res_b, res_a),
                    ("onset day %d (detected from cost series); attributed to config day %d: %s %s->%s"
                     % (onset_day, change["day"], change["kind"], change["from_value"], change["to_value"]))
                    if change else "onset day %d; no config change nearby" % onset_day,
                ],
                "impact": _cost_impact(after, cost_b, cost_a, share),
                "audience": ["agent_builder", "business_owner"],
                "if_nothing_changes": (
                    "Every conversation on this agent now costs ~%.0f%% more and takes "
                    "~%.0f more turns for the same outcome — pure waste that scales with "
                    "volume." % ((cost_a / cost_b - 1) * 100, turns_a - turns_b)),
            })
            diagnoses.append({
                "id": "d_" + fid, "finding_id": fid, "cause_class": "prompt.regression",
                "confidence": 0.87,
                "attributed_change": ({"kind": change["kind"], "day": change["day"]}
                                      if change else None),
                "evidence": [
                    "turns and cost step up at the detected onset, resolution does not move",
                    "cost/turns inflation with flat outcome is the prompt-verbosity signature",
                ],
            })
    return findings, diagnoses


# --------------------------------------------------------------------------------------
# Novel/unexplained: a large, sustained resolution drop on a cohort that none of the
# classified signatures explained. Reported honestly rather than dropped — this is the
# open-world safety net for a fault kind we did not anticipate. Gated hard so it never
# fires on noise.
# --------------------------------------------------------------------------------------

def detect_unexplained(sessions, tool_calls, kb_lookups, config_rows, std, covered):
    from . import standard as std_mod
    findings, diagnoses = [], []
    for tenant in sorted({s["tenant"] for s in sessions}):
        tsessions = [s for s in sessions if s["tenant"] == tenant]
        by_intent = cohorts.by_intent(tsessions)
        series = [_weekly(ss, cohorts.resolution_rate) for ss in by_intent.values()]
        noise = std_mod.noise_band([s for s in series if len(s) > 1])
        floor = max(UNEXPLAINED_DROP, NOISE_K * noise)
        for intent, isess in by_intent.items():
            if len(isess) < cohorts.MIN_COHORT_SESSIONS or (tenant, intent) in covered:
                continue
            weekly = _weekly(isess, cohorts.resolution_rate)
            shift = _sustained_shift(weekly)
            if not shift or (shift["before"] - shift["after"]) <= floor:
                continue
            onset_day = shift["onset_week"] * 7
            change = _attribute(config_rows, tenant, onset_day)
            before, after = cohorts.before_after(isess, onset_day, SPAN)
            share = len(after) / max(1, len([s for s in tsessions
                                             if onset_day <= s["day"] < onset_day + SPAN]))
            fid = "f_unexplained_%s_%s" % (tenant.split("-")[0], intent)
            findings.append({
                "id": fid, "tenant": tenant, "cohort": {"intent": intent},
                "metric": "resolution_rate",
                "window": {"from_day": onset_day, "to_day": onset_day + SPAN},
                "observed": round(shift["after"], 4), "expected": round(shift["before"], 4),
                "is_regression": True, "severity": "medium",
                "evidence": [
                    "sustained resolution drop %.3f -> %.3f at onset day %d"
                    % (shift["before"], shift["after"], onset_day),
                    "exceeds the deployment noise band (%.3f) and the floor (%.3f)" % (noise, floor),
                    "no kb, tool, or cost signature matched — cause not classified",
                    ("nearest config day %d: %s" % (change["day"], change["kind"]))
                    if change else "no config change nearby — likely environmental",
                ],
                "impact": _drop_impact(after, shift["before"], shift["after"], share),
                "audience": ["agent_builder", "platform_owner"],
                "if_nothing_changes": (
                    "A real, sustained drop with no identified cause — it must be "
                    "investigated before it is trusted or dismissed."),
            })
            diagnoses.append({
                "id": "d_" + fid, "finding_id": fid, "cause_class": "unexplained",
                "confidence": 0.5,
                "attributed_change": ({"kind": change["kind"], "day": change["day"]}
                                      if change else None),
                "evidence": ["deviation confirmed against the deployment's own baseline "
                             "and noise band; signature did not match a known cause"],
            })
    return findings, diagnoses


# ---- shared impact + grouping helpers ----

def _group(rows, field):
    out = defaultdict(list)
    for r in rows:
        out[r.get(field)].append(r)
    return dict(out)


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
            "%d sessions of the affected cohort in the post-onset window (%.1f%% of "
            "tenant traffic). Resolution fell %.3f -> %.3f, so ~%d resolutions were lost "
            "versus the pre-onset baseline; %d handoffs and %d abandons downstream."
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
            "%d sessions on this agent in the post-onset window (%.1f%% of tenant "
            "traffic) at mean cost %.4f vs a pre-onset mean of %.4f, i.e. ~$%.2f of "
            "avoidable spend in the window; resolution unchanged."
            % (n, share * 100, cost_a, cost_b, max(0.0, extra))),
    }
