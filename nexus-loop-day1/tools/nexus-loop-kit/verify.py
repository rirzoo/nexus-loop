#!/usr/bin/env python3
"""Confirm a generated corpus is still teachable.

    python3 verify.py --kit ../../kit
    python3 verify.py --kit ../../kit-sealed

Run this after generating a SEALED corpus, before the event. The sealed layout is
derived from a passphrase, so its fault windows land in different places every time
— and some layouts quietly destroy the lesson a fault is meant to teach. The classic
one: a volume spike sitting inside the tool fault's comparison lookback inflates the
BEFORE figure, so the declared error rate appears to FALL during the fault instead
of staying flat.

`sealed_schedule()` constrains against the failures we know about. This checks the
result rather than trusting the constraints.

Exit code 0 if every signature holds, 1 if any does not — so it can gate a release.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

TOL_FLAT_ERR = 0.012      # "flat" for a rate that sits around 0.015
TOL_FLAT_RES = 0.045      # week-to-week spread on a resolution rate


def checks(o):
    f1, f2, f3 = o["F1"], o["F2"], o["F3"]
    d1, d2, d3 = o["D1"], o["D2"], o["D3"]
    per = [v for v in d1["per_cohort_resolution_during"].values() if v]
    return [
        ("F1  cohort collapses vs peers",
         f1["cohort_resolution_in_window"] < f1["peer_intent_resolution_in_window"] - 0.20,
         "%.3f vs peer %.3f" % (f1["cohort_resolution_in_window"],
                                f1["peer_intent_resolution_in_window"])),
        ("F2  declared error rate stays FLAT",
         abs(f2["declared_tool_error_rate_during"] - f2["declared_tool_error_rate_before"]) < TOL_FLAT_ERR,
         "%.4f -> %.4f" % (f2["declared_tool_error_rate_before"],
                           f2["declared_tool_error_rate_during"])),
        ("F2  resolution actually drops",
         f2["order_status_resolution_during"] < f2["order_status_resolution_before"] - 0.04,
         "%.3f -> %.3f" % (f2["order_status_resolution_before"],
                           f2["order_status_resolution_during"])),
        ("F3  turns rise",
         f3["median_turns_during"] > f3["median_turns_before"],
         "%s -> %s" % (f3["median_turns_before"], f3["median_turns_during"])),
        ("F3  outcome stays FLAT",
         abs(f3["resolution_during"] - f3["resolution_before"]) < TOL_FLAT_RES,
         "%.3f -> %.3f" % (f3["resolution_before"], f3["resolution_during"])),
        ("D1  aggregate RISES (the trap)",
         d1["aggregate_resolution_during"] > d1["aggregate_resolution_before"],
         "%.4f -> %.4f, share %.3f -> %.3f" % (d1["aggregate_resolution_before"],
                                               d1["aggregate_resolution_during"],
                                               d1["branch_locator_share_before"],
                                               d1["branch_locator_share_during"])),
        ("D1  per-cohort rates stay together",
         max(per) - min(per) < 0.35 if len(per) > 1 else True,
         "spread %.3f" % (max(per) - min(per) if len(per) > 1 else 0)),
        ("D2  latency roughly doubles",
         d2["tool_p95_ms_during"] > 1.8 * d2["tool_p95_ms_before"],
         "p95 %s -> %s ms" % (d2["tool_p95_ms_before"], d2["tool_p95_ms_during"])),
        ("D2  quality unaffected",
         abs(d2["resolution_during"] - d2["resolution_before"]) < 0.05,
         "%.3f -> %.3f" % (d2["resolution_before"], d2["resolution_during"])),
        ("D3  judged cliff is visible",
         d3["mean_quality_before"] - d3["mean_quality_after"] > 0.40,
         "%.3f -> %.3f" % (d3["mean_quality_before"], d3["mean_quality_after"])),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="../../kit")
    a = ap.parse_args()

    gt = None
    for cand in ("ground_truth/ground_truth.json", "ground_truth_SEALED/ground_truth.json"):
        if os.path.exists(os.path.join(a.kit, cand)):
            gt = os.path.join(a.kit, cand)
            break
    if not gt:
        print("no ground truth under %s — generate the kit first" % a.kit)
        return 1

    g = json.load(open(gt))
    print("verifying %s  (corpus %s)" % (a.kit, g["variant"]))
    print("-" * 66)
    bad = 0
    for label, ok, detail in checks(g["observed_effects"]):
        print("  %-38s %-8s %s" % (label, "OK" if ok else "FAIL", detail))
        bad += not ok
    print("-" * 66)
    if bad:
        print("%d signature(s) broken — this corpus teaches the wrong lesson.\n"
              "Regenerate with a different passphrase." % bad)
        return 1
    print("all signatures hold — this corpus is fit to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
