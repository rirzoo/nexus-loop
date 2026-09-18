#!/usr/bin/env python3
"""Build the Nexus Loop day-0 kit.

    python3 generate.py --variant A --out ../../kit
    python3 generate.py --sealed "<organiser passphrase>" --out ../../kit-sealed

Stdlib only. ~80k sessions / ~900k step rows per variant in well under a minute.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nlkit import asks as asks_mod
from nlkit import catalog as catalog_mod
from nlkit import labels as labels_mod
from nlkit import world as W
from nlkit.simulate import Simulator


class JsonlGz:
    def __init__(self, path: str):
        # mtime=0 keeps the gzip header free of wall-clock time, so the same seed
        # really does produce byte-identical files run after run.
        raw = gzip.GzipFile(path, "wb", compresslevel=6, mtime=0)
        self.f = io.TextIOWrapper(raw, encoding="utf-8")
        self.n = 0

    def write(self, obj) -> None:
        self.f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self) -> None:
        self.f.close()


def measure_effects(sess: List[dict], steps_agg: Dict, v: Dict) -> Dict:
    """Read the ACTUAL effect of each fault off the generated corpus, so the
    ground truth states what happened rather than what we intended."""

    def win(rows, lo, hi, **eq):
        return [s for s in rows
                if lo <= s["day"] < hi and all(s.get(k) == val for k, val in eq.items())]

    def res(rows):
        return round(sum(1 for r in rows if r["session_end"] == "resolved") / len(rows), 4) if rows else None

    def med(rows, k):
        return statistics.median([r[k] for r in rows]) if rows else None

    def mean(rows, k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return round(sum(vals) / len(vals), 5) if vals else None

    acme = [s for s in sess if s["tenant"] == "acme-bank"]
    nw = [s for s in sess if s["tenant"] == "northwind-retail"]
    out = {}

    pc = [s for s in sess if s["intent"] == "premium_card_info"]
    out["F1"] = {
        "cohort_sessions": len(pc),
        "cohort_resolution_in_window": res(win(pc, v["kb_gap_day"], v["kb_gap_day"] + v["kb_gap_len"])),
        "tenant_resolution_before": res(win(acme, v["kb_gap_day"] - 10, v["kb_gap_day"])),
        "tenant_resolution_during": res(win(acme, v["kb_gap_day"], v["kb_gap_day"] + v["kb_gap_len"])),
        "peer_intent_resolution_in_window": res(
            win(acme, v["kb_gap_day"], v["kb_gap_day"] + v["kb_gap_len"], intent="product_info")),
        "kb_top_score_in_cohort": "see agent_steps.kb_top_score — sits in the 0.11-0.38 band",
    }

    os_ = [s for s in sess if s["intent"] == "order_status"]
    pre = range(v["silent_tool_day"] - 12, v["silent_tool_day"])
    dur = range(v["silent_tool_day"], v["silent_tool_day"] + v["silent_tool_len"])
    ea = steps_agg["order_status_tool"]
    er = lambda ds: round(sum(ea[d][1] for d in ds) / max(1, sum(ea[d][0] for d in ds)), 4)
    out["F2"] = {
        "declared_tool_error_rate_before": er(pre),
        "declared_tool_error_rate_during": er(dur),
        "declared_metric_is_flat": True,
        "order_status_resolution_before": res(win(os_, v["silent_tool_day"] - 12, v["silent_tool_day"])),
        "order_status_resolution_during": res(win(os_, v["silent_tool_day"], v["silent_tool_day"] + v["silent_tool_len"])),
        "discriminating_fields": ["step.response_bytes", "step.result_field_count",
                                  "step.retry_count on a same-tool second call"],
    }

    am = [s for s in acme if s["agent_id"] == "acme_main_v3"]
    b = win(am, v["prompt_reg_day"] - 12, v["prompt_reg_day"])
    d = win(am, v["prompt_reg_day"], v["prompt_reg_day"] + v["prompt_reg_len"])
    out["F3"] = {
        "median_turns_before": med(b, "turns"), "median_turns_during": med(d, "turns"),
        "resolution_before": res(b), "resolution_during": res(d),
        "outcome_is_flat": True,
        "cost_per_session_before": mean(b, "cost_usd"), "cost_per_session_during": mean(d, "cost_usd"),
    }

    b = win(acme, v["mix_shift_day"] - 10, v["mix_shift_day"])
    d = win(acme, v["mix_shift_day"], v["mix_shift_day"] + v["mix_shift_len"])
    share = lambda rows: round(sum(1 for r in rows if r["intent"] == "branch_locator") / len(rows), 4) if rows else None
    out["D1"] = {
        "aggregate_resolution_before": res(b), "aggregate_resolution_during": res(d),
        "aggregate_moves": "UP", "branch_locator_share_before": share(b),
        "branch_locator_share_during": share(d),
        "per_cohort_resolution_during": {
            i: res([s for s in d if s["intent"] == i])
            for i in ("branch_locator", "balance_enquiry", "txn_dispute", "product_info")},
    }

    b = win(nw, v["volume_spike_day"] - 6, v["volume_spike_day"])
    d = win(nw, v["volume_spike_day"], v["volume_spike_day"] + v["volume_spike_len"])
    out["D2"] = {
        "sessions_per_day_before": round(len(b) / 6.0, 1),
        "sessions_per_day_during": round(len(d) / float(v["volume_spike_len"]), 1),
        "resolution_before": res(b), "resolution_during": res(d),
        "tool_p95_ms_before": steps_agg["nw_tool_p95"]["before"],
        "tool_p95_ms_during": steps_agg["nw_tool_p95"]["during"],
        "quality_is_flat": True, "self_corrects": True,
    }

    q = lambda rows: round(sum(r["quality_score"] for r in rows) / len(rows), 3) if rows else None
    out["D3"] = {
        "mean_quality_before": q(win(sess, v["judge_day"] - 10, v["judge_day"])),
        "mean_quality_after": q(win(sess, v["judge_day"], v["judge_day"] + 10)),
        "cause": "rubric v1 -> v2, not agent behaviour",
        "both_tenants_simultaneously": True,
    }

    out["C1"] = {
        "v3_share_acme": round(sum(1 for s in acme if s["agent_kind"] == "v3_agent") / len(acme), 4),
        "v3_share_northwind": round(sum(1 for s in nw if s["agent_kind"] == "v3_agent") / len(nw), 4),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["A"], default="A",
                    help="the published practice corpus")
    ap.add_argument("--sealed", metavar="SECRET",
                    help="generate the SEALED day-6 corpus from an organiser passphrase. "
                         "The layout is derived from it, so this file alone cannot produce "
                         "it. Same secret always rebuilds the same corpus.")
    ap.add_argument("--out", default="kit")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="1.0 = full corpus (~80k sessions). 0.05 for a fast smoke run.")
    ap.add_argument("--dev-sample", type=float, default=0.05,
                    help="also write corpus_sample/ — a deterministic slice of whole sessions "
                         "for fast iteration. 0 disables.")
    a = ap.parse_args()

    if a.sealed is not None:
        if len(a.sealed.strip()) < 8:
            print("--sealed needs a real passphrase (8+ characters) that only the organisers "
                  "hold. A default or empty secret would let anyone rebuild the day-6 "
                  "ground truth.", file=sys.stderr)
            return 2
        schedule, label, sealed = W.sealed_schedule(a.sealed), "SEALED", True
    else:
        schedule, label, sealed = W.FAULT_SCHEDULE[a.variant], a.variant, False

    t0 = time.time()
    out = os.path.abspath(a.out)
    corpus = os.path.join(out, "corpus")
    labels_dir = os.path.join(out, "labels")
    gt_dir = os.path.join(out, "ground_truth_SEALED" if sealed else "ground_truth")
    for d in (corpus, labels_dir, gt_dir):
        os.makedirs(d, exist_ok=True)

    sw = JsonlGz(os.path.join(corpus, "sessions.jsonl.gz"))
    pw = JsonlGz(os.path.join(corpus, "agent_steps.jsonl.gz"))
    tw = JsonlGz(os.path.join(corpus, "turns.jsonl.gz"))

    # A whole-session slice for iterating fast. Sampling SESSIONS (never rows) keeps
    # every conversation intact, so a join that works on the sample works on the full
    # corpus. Rates hold; small cohorts get noisy, so confirm findings on the full set.
    sample = None
    if a.dev_sample > 0:
        sdir = os.path.join(out, "corpus_sample")
        os.makedirs(sdir, exist_ok=True)
        sample = {n: JsonlGz(os.path.join(sdir, n + ".jsonl.gz"))
                  for n in ("sessions", "agent_steps", "turns")}
        keep_every = max(1, int(round(1.0 / a.dev_sample)))

    sessions: List[dict] = []
    order_tool = defaultdict(lambda: [0, 0])
    nw_tool_lat = defaultdict(list)
    step_type_counts = defaultdict(int)

    sim = Simulator(schedule, a.seed, a.scale, label=label)
    n_seen = 0
    for sess, steps, turns in sim.run():
        sessions.append(sess)
        sw.write(sess)
        in_sample = sample is not None and (n_seen % keep_every == 0)
        n_seen += 1
        if in_sample:
            sample["sessions"].write(sess)
        for r in steps:
            pw.write(r)
            if in_sample:
                sample["agent_steps"].write(r)
            step_type_counts[r["step_type"]] += 1
            if r["step_type"] == "tool_call" and r["tenant"] == "northwind-retail":
                nw_tool_lat[r["day"]].append(r["duration_ms"])
                if r.get("tool_name") == "get_order_status":
                    order_tool[r["day"]][0] += 1
                    if r["outcome"] != "ok":
                        order_tool[r["day"]][1] += 1
        for r in turns:
            tw.write(r)
            if in_sample:
                sample["turns"].write(r)
    for w in (sw, pw, tw):
        w.close()
    if sample:
        for w in sample.values():
            w.close()

    v = schedule

    def p95(days):
        vals = sorted(x for d in days for x in nw_tool_lat[d])
        return vals[int(len(vals) * 0.95)] if vals else None

    steps_agg = {
        "order_status_tool": order_tool,
        "nw_tool_p95": {
            "before": p95(range(v["volume_spike_day"] - 6, v["volume_spike_day"])),
            "during": p95(range(v["volume_spike_day"], v["volume_spike_day"] + v["volume_spike_len"])),
        },
    }

    # --- config timeline ----------------------------------------------------
    with open(os.path.join(corpus, "config_timeline.csv"), "w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["day", "date", "tenant", "kind", "target", "from_value", "to_value", "note"])
        for c in W.config_timeline(schedule):
            date = (W.EPOCH.date().toordinal() + c.day)
            import datetime as _dt
            wcsv.writerow([c.day, _dt.date.fromordinal(date).isoformat(), c.tenant, c.kind,
                           c.target, c.from_value, c.to_value, c.note])

    # --- feedback (csat only, sparse) ---------------------------------------
    with open(os.path.join(corpus, "feedback.csv"), "w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["session_id", "tenant", "day", "csat"])
        nfb = 0
        for s in sessions:
            if s["csat"] is not None:
                wcsv.writerow([s["session_id"], s["tenant"], s["day"], s["csat"]])
                nfb += 1

    # --- labels -------------------------------------------------------------
    lab = labels_mod.sample(sessions, a.seed)
    for name, rows in lab.items():
        with open(os.path.join(labels_dir, name + ".jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    # --- catalog / asks -----------------------------------------------------
    with open(os.path.join(out, "catalog.json"), "w") as f:
        json.dump(catalog_mod.build(label), f, indent=2)
    with open(os.path.join(out, "asks.md"), "w") as f:
        f.write(asks_mod.render())

    # --- ground truth (organiser only) --------------------------------------
    gt = W.ground_truth(schedule, label)
    gt["observed_effects"] = measure_effects(sessions, steps_agg, schedule)
    with open(os.path.join(gt_dir, "ground_truth.json"), "w") as f:
        json.dump(gt, f, indent=2)

    # --- manifest -----------------------------------------------------------
    manifest = {
        "kit_version": "1.0.0",
        "corpus_variant": label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seed": a.seed, "scale": a.scale,
        "days": W.CORPUS_DAYS, "epoch": W.EPOCH.isoformat(),
        "tenants": [t.key for t in W.TENANTS],
        "rows": {"sessions": sw.n, "agent_steps": pw.n, "turns": tw.n,
                 "config_changes": len(W.config_timeline(schedule)),
                 "csat_responses": nfb,
                 "outcome_labels": len(lab["outcome_labels"]),
                 "rubric_scores": len(lab["rubric_scores"])},
        "step_type_counts": dict(step_type_counts),
        "files": {
            "corpus/sessions.jsonl.gz": "one row per conversation",
            "corpus/agent_steps.jsonl.gz": "one row per execution step — the fact grain",
            "corpus/turns.jsonl.gz": "transcript text",
            "corpus/config_timeline.csv": "every config change, timestamped",
            "corpus/feedback.csv": "sparse CSAT",
            "catalog.json": "the semantic catalog — plan over this, not the raw schema",
            "asks.md": "the eleven operator asks",
            "labels/outcome_labels.jsonl": "human resolved / by-design labels",
            "labels/rubric_scores.jsonl": "human 1-5 quality scores",
            "corpus_sample/": "a whole-session slice (~%d%%) for fast iteration — same schema, "
                              "same joins, noisier small cohorts" % int(a.dev_sample * 100),
        },
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    size = sum(os.path.getsize(os.path.join(dp, fn))
               for dp, _, fns in os.walk(out) for fn in fns) / 1e6
    print("kit written to %s" % out)
    print("  variant   %s%s" % (label, "  (do not distribute ground_truth_SEALED/)" if sealed else ""))
    print("  sessions  %d" % sw.n)
    print("  steps     %d  %s" % (pw.n, dict(step_type_counts)))
    print("  turns     %d" % tw.n)
    print("  labels    %d outcome / %d rubric" % (len(lab["outcome_labels"]), len(lab["rubric_scores"])))
    if sample:
        print("  sample    %d sessions / %d steps in corpus_sample/"
              % (sample["sessions"].n, sample["agent_steps"].n))
    print("  on disk   %.1f MB" % size)
    print("  elapsed   %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
