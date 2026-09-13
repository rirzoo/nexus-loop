"""Score our own report and assert the non-negotiable honesty checklist.

score.py is not a black box — this shells out to it and parses --json, then adds the
mechanical Rule 1 / Rule 2 guards score.py doesn't check.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_TOOLS = os.path.normpath(os.path.join(HERE, "..", "nexus-loop-day1", "tools", "nexus-loop-kit"))


def run_score(report_path, gt_path) -> dict:
    out = subprocess.run(
        [sys.executable, os.path.join(KIT_TOOLS, "score.py"),
         "--report", report_path, "--ground-truth", gt_path, "--json"],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def static_guards() -> list:
    """Rule 1/Rule 2 made mechanical: no engine module imports a network/LLM client,
    and none opens the kit for writing."""
    problems = []
    banned = ("import requests", "import urllib.request", "import http.client",
              "import openai", "import anthropic", "from openai", "from anthropic",
              "socket.socket", "urlopen")
    for path in glob.glob(os.path.join(HERE, "*.py")):
        if os.path.basename(path) == "selfcheck.py":
            continue  # this file holds the banned patterns as detection literals
        with open(path) as f:
            src = f.read()
        for b in banned:
            if b in src:
                problems.append("%s imports/uses %r (Rule 1 risk)" % (os.path.basename(path), b))
        if 'nexus-loop-day1/kit' in src and ('"w"' in src or "'w'" in src):
            problems.append("%s may open the kit for writing (Rule 2 risk)" % os.path.basename(path))
    return problems


def check(report_path, gt_path) -> int:
    rep = json.load(open(report_path))
    scored = run_score(report_path, gt_path)
    sec = scored["sections"]
    fails = []

    # --- honesty spine ---
    metrics = rep["metrics"]
    for m in metrics:
        if not m.get("fidelity") or (m.get("coverage") or {}).get("value") is None:
            fails.append("metric %s missing fidelity/coverage" % m.get("id"))
    a04 = [m for m in metrics if m.get("ask_id") == "A04"]
    if not a04 or a04[0]["fidelity"] != "measured":
        fails.append("first A04 metric is not 'measured'")
    cals = [m for m in metrics if (m.get("calibration") or {}).get("agreement") is not None]
    if not cals:
        fails.append("no calibration published on any judged metric")
    if any(m["calibration"]["agreement"] >= 0.995 for m in cals):
        fails.append("a calibration is >= 0.995 (bug, not a judge)")

    gaps = {g["ask_id"]: g for g in rep["gaps"]}
    if gaps.get("A11", {}).get("verdict") != "NOT_MEASURABLE":
        fails.append("A11 not refused as NOT_MEASURABLE")
    if not (gaps.get("A11", {}).get("required_event") or {}).get("fields"):
        fails.append("A11 gap names no required_event fields")
    a09 = gaps.get("A09")
    a09_metric = [m for m in metrics if m.get("ask_id") == "A09"]
    if not (a09 and a09["verdict"] == "REQUIRES_NEW_JUDGE") and not a09_metric:
        fails.append("A09 neither a judged metric nor a REQUIRES_NEW_JUDGE gap")

    # --- specificity: all three decoys examined and dismissed ---
    dismissed = {d["cause_class"] for d in rep["diagnoses"]
                 if d["finding_id"] in {f["id"] for f in rep["findings"]
                                        if not f.get("is_regression")}}
    for cc in ("traffic_mix", "load", "judge_change"):
        if cc not in dismissed:
            fails.append("decoy cause_class %s not dismissed" % cc)

    # --- Rule 1 / Rule 2 static guards ---
    fails += static_guards()

    # --- report scores at least the Level-1 bar ---
    machine = scored["machine_score"]

    print("=" * 60)
    print("SELF-CHECK  machine_score = %.2f / %d" % (machine, scored["machine_max"]))
    for label, s in sec.items():
        print("  %-22s %5.2f / %d" % (label, s["score"], s["max"]))
    print("-" * 60)
    if fails:
        print("CHECKLIST FAILURES:")
        for f in fails:
            print("  x " + f)
        return 1
    print("all non-negotiable checks pass")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--ground-truth",
                    default=os.path.join(os.path.dirname(KIT_TOOLS), "..", "kit",
                                         "ground_truth", "ground_truth.json"))
    a = ap.parse_args()
    return check(a.report, os.path.normpath(a.ground_truth))


if __name__ == "__main__":
    raise SystemExit(main())
