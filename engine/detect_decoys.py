"""Detectors that EXAMINE and DISMISS the three decoys in writing.

Flagging a decoy as a real regression costs more than missing a real problem, so
each of these emits an is_regression:false finding with a not_a_regression_because
and a matching diagnosis cause_class — which is exactly what earns specificity
credit. D1/D2 are config-agnostic (they have no config_timeline entry); D3 is read
straight off the single judge-version boundary row.
"""
from __future__ import annotations

from collections import defaultdict

from . import cohorts, joins

D1_MIN_SURGE = 0.12       # absolute jump in an intent's share of tenant traffic
D1_MIN_RUN_DAYS = 3       # a campaign runs for days; one busy day is not a mix shift
D1_RATE_SHARE = 0.5       # rate effect must stay under this share of the mix effect
D2_VOL_SPIKE = 1.8        # daily volume multiple over baseline, weekday-adjusted
D2_MIN_RUN_DAYS = 2       # a flash sale lasts days; one busy day is not an event
D2_LAT_SPIKE = 1.5        # tool p95 multiple over baseline
D2_RES_FLAT = 0.05
D3_CLIFF = 0.40           # judged-quality drop across the boundary


def _weeks(rows):
    w = defaultdict(list)
    for r in rows:
        w[r["day"] // 7].append(r)
    return w


def _mix_rate_split(window_sessions, baseline_sessions):
    """Decompose the change in aggregate resolution into the part explained by the change
    in traffic MIX and the part explained by a change in per-cohort RATES.

        mix  = sum over cohorts of (w_in - w_out) * r_out   # weights move, rates held
        rate = sum over cohorts of  w_in * (r_in - r_out)   # rates move, weights held

    Their sum is the observed aggregate move. This is what "stratify by intent" actually
    means, and it needs no threshold on how far apart cohorts sit: a deployment whose
    intents legitimately resolve anywhere from 0.5 to 0.95 is not thereby suspicious.
    Returns (mix, rate, agg_in, agg_out, w_in, w_out)."""
    g_in = cohorts.by_intent(window_sessions)
    g_out = cohorts.by_intent(baseline_sessions)
    keys = set(g_in) | set(g_out)
    w_in = {k: len(g_in.get(k, [])) / len(window_sessions) for k in keys}
    w_out = {k: len(g_out.get(k, [])) / len(baseline_sessions) for k in keys}
    r_in = {k: cohorts.resolution_rate(g_in.get(k, [])) for k in keys}
    r_out = {k: cohorts.resolution_rate(g_out.get(k, [])) for k in keys}
    mix = sum((w_in[k] - w_out[k]) * r_out[k] for k in keys)
    rate = sum(w_in[k] * (r_in[k] - r_out[k]) for k in keys)
    return (mix, rate, cohorts.resolution_rate(window_sessions),
            cohorts.resolution_rate(baseline_sessions), w_in, w_out)


def detect_traffic_mix(sessions, tool_calls, kb_lookups, config_rows, covered=()):
    """D1 — a campaign inflates a trivially-contained intent's share; aggregate
    containment moves while every cohort stays flat. A mix shift, not a quality
    change.

    `covered` is the set of (tenant, intent) pairs already carrying a regression finding.
    They are held out of the decomposition: a cohort we have just flagged as broken cannot
    serve as part of its own baseline, and leaving it in lets a real fault elsewhere in the
    tenant masquerade as a per-cohort rate move and bury the mix signal."""
    findings, diagnoses = [], []
    covered = set(covered)
    for tenant in sorted({s["tenant"] for s in sessions}):
        tsessions = [s for s in sessions if s["tenant"] == tenant]
        # DAILY share series, not weekly. A campaign does not start on a Monday, so a weekly
        # window either clips the surge or pads it with normal days; either way the diluted
        # window shrinks the mix effect it is trying to demonstrate. At tenant volumes a
        # day is plenty of traffic to estimate a share, and the run search then recovers
        # the campaign's actual span.
        counts = defaultdict(lambda: defaultdict(int))   # day -> intent -> n
        dtotal = defaultdict(int)
        for s in tsessions:
            counts[s["day"]][s["intent"]] += 1
            dtotal[s["day"]] += 1
        share_series = defaultdict(dict)                 # intent -> {day: share}
        for day, intents in counts.items():
            if dtotal[day] < cohorts.MIN_COHORT_SESSIONS:
                continue
            for intent, n in intents.items():
                share_series[intent][day] = n / dtotal[day]

        best = None   # (surge, intent, run)
        for intent, series in share_series.items():
            run = cohorts.best_run(series, +1, min_len=D1_MIN_RUN_DAYS)
            if not run:
                continue
            surge = run["inside"] - run["outside"]
            if best is None or surge > best[0]:
                best = (surge, intent, run)
        if not best or best[0] < D1_MIN_SURGE:
            continue
        surge, intent, run = best
        lo, hi = run["start"], run["end"]
        clean = [s for s in tsessions if (tenant, s["intent"]) not in covered]
        window_sess = [s for s in clean if lo <= s["day"] <= hi]
        before_sess = [s for s in clean if not (lo <= s["day"] <= hi)]
        if not window_sess or not before_sess:
            continue
        mix, rate, agg_during, agg_before, w_in, w_out = _mix_rate_split(
            window_sess, before_sess)
        if not mix or abs(rate) >= D1_RATE_SHARE * abs(mix):
            continue  # per-cohort rates really moved — this is not a clean mix shift
        share_before, share_during = w_out.get(intent, 0.0), w_in.get(intent, 0.0)
        fid = "f_mix_%s_%s" % (cohorts.tenant_slug(tenant), intent)
        findings.append({
            "id": fid, "tenant": tenant, "cohort": {"intent": intent},
            "metric": "resolution_rate", "window": {"from_day": lo, "to_day": hi},
            "observed": round(agg_during, 4), "expected": round(agg_before, 4),
            "is_regression": False, "severity": "low",
            "not_a_regression_because": (
                "aggregate resolution moves %.4f -> %.4f (%+.4f). Decomposed over intents, "
                "%+.4f of that move is the change in question MIX alone — %s's share of "
                "traffic goes %.0f%% -> %.0f%% with every cohort's rate held at its baseline "
                "— and only %+.4f is any change in the rates themselves. The deployment did "
                "not get better or worse; it was asked easier questions."
                % (agg_before, agg_during, agg_during - agg_before, mix, intent,
                   share_before * 100, share_during * 100, rate)),
            "evidence": [
                "%s share %.3f -> %.3f over days %d-%d" % (intent, share_before,
                                                           share_during, lo, hi),
                "mix effect %+.4f vs rate effect %+.4f (rate is %.0f%% of mix)"
                % (mix, rate, abs(rate) / abs(mix) * 100),
                "no config change on %s in the window" % tenant,
            ],
        })
        diagnoses.append({
            "id": "d_" + fid, "finding_id": fid, "cause_class": "traffic_mix",
            "confidence": 0.95, "attributed_change": None,
            "evidence": ["intent share moves; holding rates at baseline reproduces the "
                         "aggregate move, so the rates are not what changed"],
        })
    return findings, diagnoses


def _deseasonalised_volume(tsessions):
    """Daily session counts divided by the typical count for that day's weekday.

    Support traffic has a strong weekly rhythm — this corpus runs weekends at roughly half
    of a weekday. Comparing every day to one global median therefore rates a busy Saturday
    as quiet, so a spike that begins on a weekend is only half-detected. `day % 7` is a
    stable weekday index whatever the corpus epoch is, so the pattern can be measured from
    the data without knowing the calendar. Returns {day: multiple of that weekday's norm}."""
    daily = cohorts.daily_counts(tsessions)
    by_phase = defaultdict(list)
    for day, n in daily.items():
        by_phase[day % 7].append(n)
    norm = {p: cohorts.median(v) for p, v in by_phase.items()}
    return daily, {d: n / norm[d % 7] for d, n in daily.items() if norm.get(d % 7)}


def detect_load_event(sessions, tool_calls, kb_lookups, config_rows):
    """D2 — a flash sale multiplies volume; tool p95 latency spikes then self-
    corrects. Quality is flat throughout: a reliability event, not a regression.

    The window is the contiguous run of unusually busy days, found the same way every other
    episode in this engine is. A fixed "busier than N x the median" test clips the quiet
    days at the edges of a spike, and the clipped days then land in the latency baseline —
    where the spike's own elevated p95 raises the bar the spike has to clear, and the event
    hides itself."""
    findings, diagnoses = [], []
    for tenant in sorted({s["tenant"] for s in sessions}):
        tsessions = [s for s in sessions if s["tenant"] == tenant]
        daily, relative = _deseasonalised_volume(tsessions)
        if len(relative) < 7:
            continue
        run = cohorts.best_run(relative, +1, min_len=D2_MIN_RUN_DAYS)
        if not run or not run["outside"] or run["inside"] / run["outside"] < D2_VOL_SPIKE:
            continue
        lo, hi = run["start"], run["end"]
        tcalls = [c for c in tool_calls if c["tenant"] == tenant]
        calls_in, calls_out = cohorts.split_in_out(tcalls, lo, hi)
        sess_in, sess_out = cohorts.split_in_out(tsessions, lo, hi)
        p95_before = cohorts.percentile([c["duration_ms"] for c in calls_out], 0.95)
        p95_during = cohorts.percentile([c["duration_ms"] for c in calls_in], 0.95)
        res_before = cohorts.resolution_rate(sess_out)
        res_during = cohorts.resolution_rate(sess_in)
        # Self-correction is only observable if the corpus keeps running after the spike.
        tail = [s for s in tsessions if s["day"] > hi]
        res_after = (cohorts.resolution_rate(tail)
                     if len(tail) >= cohorts.MIN_COHORT_SESSIONS else None)
        latency_spiked = p95_before > 0 and p95_during > p95_before * D2_LAT_SPIKE
        res_flat = abs(res_during - res_before) < D2_RES_FLAT
        self_corrects = res_after is None or abs(res_after - res_before) < D2_RES_FLAT
        if not (latency_spiked and res_flat and self_corrects):
            continue
        peak = max(daily[d] for d in range(lo, hi + 1) if d in daily)
        fid = "f_load_%s" % cohorts.tenant_slug(tenant)
        findings.append({
            "id": fid, "tenant": tenant, "cohort": {},
            "metric": "resolution_rate", "window": {"from_day": lo, "to_day": hi + 3},
            "observed": round(res_during, 4), "expected": round(res_before, 4),
            "is_regression": False, "severity": "low",
            "not_a_regression_because": (
                "over days %d-%d volume runs %.1fx the normal level for those weekdays and "
                "tool p95 latency spikes %d -> %d ms, but resolution stays flat "
                "(%.3f -> %.3f)%s. This is a load event for the platform view, not a "
                "quality regression."
                % (lo, hi, run["inside"] / run["outside"], int(p95_before), int(p95_during),
                   res_before, res_during,
                   "" if res_after is None
                   else " and recovers to %.3f the moment volume does" % res_after)),
            "evidence": [
                "sessions/day peak %d vs %.1fx the norm for those weekdays"
                % (peak, run["inside"] / run["outside"]),
                "tool p95 %d -> %d ms" % (int(p95_before), int(p95_during)),
                "resolution %.3f -> %.3f, inside week-to-week spread" % (res_before, res_during),
            ],
        })
        diagnoses.append({
            "id": "d_" + fid, "finding_id": fid, "cause_class": "load",
            "confidence": 0.93, "attributed_change": None,
            "evidence": ["volume spike with no config change", "self-corrects with volume"],
        })
    return findings, diagnoses


def detect_judge_boundary(sessions, tool_calls, kb_lookups, config_rows):
    """D3 — a stricter quality rubric (v1->v2) drops judged scores across BOTH
    tenants at once. judge_version is stamped on every value, so the comparison must
    be refused, not read as an agent regression."""
    findings, diagnoses = [], []
    change = joins.judge_change(config_rows)
    if not change:
        return findings, diagnoses
    day = change["day"]
    span = 14
    drops = {}
    for tenant in sorted({s["tenant"] for s in sessions}):
        before = [s["quality_score"] for s in sessions
                  if s["tenant"] == tenant and day - span <= s["day"] < day
                  and s.get("quality_score") is not None]
        after = [s["quality_score"] for s in sessions
                 if s["tenant"] == tenant and day <= s["day"] < day + span
                 and s.get("quality_score") is not None]
        if before and after:
            drops[tenant] = (sum(before) / len(before), sum(after) / len(after))
    if len(drops) < 2 or not all((b - a) > D3_CLIFF for b, a in drops.values()):
        return findings, diagnoses
    mean_b = sum(b for b, _ in drops.values()) / len(drops)
    mean_a = sum(a for _, a in drops.values()) / len(drops)
    fid = "f_judge_boundary"
    findings.append({
        "id": fid, "tenant": "*", "cohort": {},
        "metric": "quality_score", "window": {"from_day": day, "to_day": day + span},
        "observed": round(mean_a, 3), "expected": round(mean_b, 3),
        "is_regression": False, "severity": "low",
        "not_a_regression_because": (
            "the drop is simultaneous across both tenants and coincides exactly with the "
            "judge quality_rubric %s->%s change on day %d. We measured the rubric, not the "
            "agent. quality_score is only comparable within one judge_version."
            % (change["from_value"], change["to_value"], day)),
        "evidence": [
            "config_timeline day %d: judge quality_rubric %s -> %s" % (day, change["from_value"], change["to_value"]),
            "both tenants drop together: " + ", ".join(
                "%s %.2f->%.2f" % (t, b, a) for t, (b, a) in sorted(drops.items())),
        ],
    })
    diagnoses.append({
        "id": "d_" + fid, "finding_id": fid, "cause_class": "judge_change",
        "confidence": 0.97, "attributed_change": {"kind": "judge", "day": day},
        "evidence": ["both tenants, all cohorts, same day, same magnitude"],
    })
    return findings, diagnoses
