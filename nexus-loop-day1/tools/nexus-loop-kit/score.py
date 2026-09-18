#!/usr/bin/env python3
"""The judging harness.

    55 points are scored here. The other 45 are judged by people — the decision layer,
    the screen, and how honestly the work is presented. This script cannot read a screen,
    so it prints a JUDGES' CHECKLIST of what is objectively present, and humans score the
    quality of it. A pipeline that finds every fault and explains none of it to anyone
    tops out at 55.

    python3 score.py --report team-x/loop-report.json \
                     --ground-truth kit-sealed/ground_truth_SEALED/ground_truth.json \
                     --catalog kit-sealed/catalog.json

Scores the four machine-checkable criteria (55 of 100). The remaining 45 —
decision quality, the screen, and framing — are judged by humans and printed as
an open slot, because a script cannot read a screen.

Teams: run this against variant A all week. It is not a black box.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

W_ACCURACY, W_SPECIFICITY, W_LOOP, W_HONESTY = 20, 15, 8, 12
W_HUMAN = 45
W_MACHINE = W_ACCURACY + W_SPECIFICITY + W_LOOP + W_HONESTY

START_TOLERANCE = 3      # days early a finding may start and still count
DETECT_GRACE = 10        # days after onset within which detection still scores


def load(p: str) -> dict:
    with open(p) as f:
        return json.load(f)


def cohort_matches(finding_cohort: dict, fault_cohort: dict) -> bool:
    if not fault_cohort:
        return True
    return any(str(finding_cohort.get(k, "")).lower() == str(v).lower()
               for k, v in fault_cohort.items())


def window_overlaps(fw: dict, onset: int, length: int) -> bool:
    lo, hi = fw.get("from_day", -999), fw.get("to_day", 999)
    return not (hi < onset - START_TOLERANCE or lo > onset + length)


def match_fault(f: dict, faults: List[dict]) -> Optional[dict]:
    for flt in faults:
        if f.get("tenant") != flt["tenant"]:
            continue
        if not cohort_matches(f.get("cohort") or {}, flt.get("cohort") or {}):
            continue
        if window_overlaps(f.get("window") or {}, flt["onset_day"], flt["duration_days"]):
            return flt
    return None


def match_decoy(f: dict, decoys: List[dict]) -> Optional[dict]:
    for d in decoys:
        if d["tenant"] != "*" and f.get("tenant") != d["tenant"]:
            continue
        if window_overlaps(f.get("window") or {}, d["onset_day"], d["duration_days"]):
            return d
    return None


# --------------------------------------------------------------------------- #

def score_accuracy(rep, gt, notes):
    faults = gt["faults"]
    findings = [f for f in rep.get("findings", []) if f.get("is_regression")]
    diags = {d["finding_id"]: d for d in rep.get("diagnoses", [])}
    per = W_ACCURACY / float(len(faults))
    total = 0.0
    for flt in faults:
        hits = [f for f in findings if match_fault(f, [flt])]
        if not hits:
            notes.append("MISS  %s (%s / %s) — not detected"
                         % (flt["id"], flt["tenant"], flt["cause_class"]))
            continue
        best = min(hits, key=lambda f: abs((f["window"].get("from_day", 0)) - flt["onset_day"]))
        lag = max(0, best["window"].get("from_day", 0) - flt["onset_day"])
        # detection: 45% of the slice, decayed by how late it was noticed
        det = 0.45 * max(0.35, 1.0 - (lag / float(DETECT_GRACE)) * 0.65)
        # localisation: 20% for naming the right cohort key, not just the tenant
        loc = 0.20 if cohort_matches(best.get("cohort") or {}, flt.get("cohort") or {}) else 0.0
        # diagnosis: 35% for the right cause class
        d = diags.get(best["id"])
        dg = 0.0
        if d:
            if d.get("cause_class") == flt["cause_class"]:
                dg = 0.35
            elif d.get("cause_class", "").split(".")[0] == flt["cause_class"].split(".")[0]:
                dg = 0.18
        # attribution bonus folded into diagnosis: right change marker, right day
        ac = (d or {}).get("attributed_change") or {}
        if dg and ac.get("kind") == flt["attributable_change"]["kind"] \
                and abs(int(ac.get("day", -99)) - flt["attributable_change"]["day"]) <= 2:
            dg = min(0.35, dg + 0.05)
        got = (det + loc + dg) * per
        total += got
        notes.append("HIT   %s  lag=%dd  cohort=%s  cause=%s  -> %.1f/%.1f"
                     % (flt["id"], lag, "ok" if loc else "MISSED",
                        (d or {}).get("cause_class", "none"), got, per))
    return round(total, 2)


def score_specificity(rep, gt, notes):
    decoys = gt["decoys"]
    faults = gt["faults"]
    flagged = [f for f in rep.get("findings", []) if f.get("is_regression")]
    diags = {d["finding_id"]: d for d in rep.get("diagnoses", [])}

    if not rep.get("findings"):
        notes.append("no findings at all — specificity is what you earn by LOOKING and being "
                     "right, not by staying silent")
        return 0.0

    score = float(W_SPECIFICITY)
    per_decoy = W_SPECIFICITY * 0.28
    for d in decoys:
        bad = [f for f in flagged if match_decoy(f, [d]) and not match_fault(f, faults)]
        if bad:
            score -= per_decoy
            notes.append("FALSE ALARM on %s (%s) — %d finding(s): %s"
                         % (d["id"], d["kind"], len(bad), ", ".join(f["id"] for f in bad)))
        else:
            # credit for explicitly examining and dismissing it
            expect = {"traffic_mix_shift": "traffic_mix", "load_event": "load",
                      "judge_version_boundary": "judge_change"}[d["kind"]]
            seen = [f for f in rep.get("findings", [])
                    if match_decoy(f, [d]) and not f.get("is_regression")
                    and f.get("not_a_regression_because")
                    and diags.get(f["id"], {}).get("cause_class") == expect]
            if seen:
                notes.append("CLEAN %s — examined and correctly dismissed" % d["id"])
            elif any(f.get("is_regression") for f in rep.get("findings", [])):
                score -= per_decoy * 0.35
                notes.append("QUIET %s — not flagged, but never examined either "
                             "(partial credit)" % d["id"])
            else:
                score -= per_decoy
                notes.append("QUIET %s — nothing was flagged anywhere, so avoiding it "
                             "proves nothing" % d["id"])
    stray = [f for f in flagged if not match_fault(f, faults) and not match_decoy(f, decoys)]
    if stray:
        pen = min(W_SPECIFICITY * 0.20, 0.6 * len(stray))
        score -= pen
        notes.append("STRAY %d finding(s) matching neither a fault nor a decoy (-%.1f)"
                     % (len(stray), pen))
    return round(max(0.0, score), 2)


def score_loop(rep, gt, notes):
    s = 0.0
    mids = {m["id"] for m in rep.get("metrics", [])}
    fids = {f["id"] for f in rep.get("findings", [])}
    dids = {d["id"] for d in rep.get("diagnoses", [])}
    pids = {p["id"] for p in rep.get("prescriptions", [])}

    if rep.get("metrics"):
        s += 2.0
    if rep.get("standard"):
        s += 3.0
        notes.append("standard: %d cohort standards mined" % len(rep["standard"]))
    else:
        notes.append("no standard mined — step 4 of the loop is missing")

    linked_d = [d for d in rep.get("diagnoses", []) if d.get("finding_id") in fids]
    if linked_d:
        s += 2.5
    if len(linked_d) < len(rep.get("diagnoses", [])):
        notes.append("some diagnoses reference a finding_id that does not exist")

    linked_p = [p for p in rep.get("prescriptions", []) if p.get("diagnosis_id") in dids]
    if linked_p:
        s += 2.5
    with_pred = [p for p in linked_p if (p.get("predicted_delta") or {}).get("to") is not None]
    if with_pred:
        s += 2.0
    else:
        notes.append("prescriptions carry no predicted delta — nothing to score later against")

    vers = [v for v in rep.get("verifications", []) if v.get("prescription_id") in pids]
    real = [v for v in vers if str(v.get("replay_run_id", "")).startswith("rp_")]
    if real:
        s += 3.5
        improved = [v for v in real if v.get("verdict") == "improved"]
        notes.append("verification: %d replayed, %d improved" % (len(real), len(improved)))
        if improved:
            s += 1.0
    else:
        notes.append("no verification against the replay endpoint — a fix with no "
                     "before/after is a hope, not a fix")

    sa = rep.get("self_assessment") or {}
    if sa.get("prescription_accuracy"):
        s += 1.5
        if int(sa.get("cycles", 0)) >= 3:
            s += 1.5
            notes.append("return arrow: %d cycles of prediction-vs-outcome recorded"
                         % sa["cycles"])
        else:
            notes.append("self_assessment present but only %d cycle(s) — priors carry no "
                         "weight yet" % int(sa.get("cycles", 0)))
        if sa.get("downweighted"):
            s += 0.5
            notes.append("return arrow: loop de-weights %s" % ", ".join(sa["downweighted"][:3]))
    else:
        notes.append("no self_assessment — the return arrow is open, this is a pipeline "
                     "not a loop")
    return round(min(float(W_LOOP), s), 2)


def score_honesty(rep, gt, notes):
    s = 0.0
    metrics = rep.get("metrics", [])
    if not metrics:
        notes.append("no metrics declared")
        return 0.0

    full = [m for m in metrics if m.get("fidelity") and (m.get("coverage") or {}).get("value") is not None]
    frac = len(full) / float(len(metrics))
    s += 4.0 * frac
    if frac < 1.0:
        notes.append("%d/%d metrics carry both fidelity and coverage"
                     % (len(full), len(metrics)))

    # coverage claims on tool-derived metrics must match reality
    trap = next(t for t in gt["coverage_traps"] if t["id"] == "C1")
    tol = trap.get("tolerance", 0.03)
    tool_metrics = [m for m in metrics
                    if "tool" in (m.get("plan", {}).get("source", "") + m.get("name", "")).lower()
                    or (m.get("plan", {}).get("filter", "")).find("tool_call") >= 0]
    if tool_metrics:
        ok = 0
        for m in tool_metrics:
            claimed = (m.get("coverage") or {}).get("value")
            for ten, true_cov in trap["true_coverage"].items():
                if abs((claimed or 0) - true_cov) <= tol:
                    ok += 1
                    break
        if ok:
            s += 3.0
            notes.append("coverage on tool metrics matches the v2 hole (%d/%d)"
                         % (ok, len(tool_metrics)))
        else:
            notes.append("tool-derived metrics claim coverage that ignores v2 traffic "
                         "(true ~%.2f)" % list(trap["true_coverage"].values())[0])
    else:
        notes.append("no tool-derived metric declared — coverage trap C1 untested")

    # calibration present, and not a suspiciously perfect 1.0
    cal = [m for m in metrics if (m.get("calibration") or {}).get("agreement") is not None]
    if cal:
        s += 2.0
        perfect = [m for m in cal if m["calibration"]["agreement"] >= 0.995]
        if perfect:
            s -= 1.0
            notes.append("calibration of 1.00 reported on %d metric(s) — the label set has "
                         "~7.5%% human disagreement baked in, so this is a bug not a judge"
                         % len(perfect))
        else:
            notes.append("calibration published (%d metric(s))" % len(cal))
    else:
        notes.append("no calibration published on any judged metric")

    # the unanswerable ask
    gaps = {g["ask_id"]: g for g in rep.get("gaps", [])}
    for ua in gt["unanswerable_asks"]:
        g = gaps.get(ua["ask_id"])
        if not g:
            notes.append("MISSED the unanswerable ask %s — no gap spec" % ua["ask_id"])
            continue
        if g.get("verdict") == ua["verdict"]:
            s += 3.0
            req = (g.get("required_event") or {}).get("fields") or []
            want = set(ua["required_event"]["fields"])
            if want & set(req):
                s += 1.5
                notes.append("gap spec for %s names the required event and %d/%d of its fields"
                             % (ua["ask_id"], len(want & set(req)), len(want)))
            else:
                notes.append("gap spec for %s is right but names no usable event spec" % ua["ask_id"])
            if g.get("nearest_proxy") and g.get("why_the_proxy_misleads"):
                s += 0.5
        else:
            notes.append("%s answered with verdict %r — it is %s"
                         % (ua["ask_id"], g.get("verdict"), ua["verdict"]))

    # fidelity traps
    by_ask = {}
    for m in metrics:
        by_ask.setdefault(m.get("ask_id"), []).append(m)
    for ft in gt["fidelity_traps"]:
        ms = by_ask.get(ft["ask_id"]) or []
        g = gaps.get(ft["ask_id"])
        if not ms and not g:
            continue
        got = (ms[0].get("fidelity") if ms else None) or (
            "judged" if (g or {}).get("verdict") == "REQUIRES_NEW_JUDGE" else None)
        if got == ft["correct_fidelity"]:
            s += 0.75
            notes.append("fidelity correct on %s (%s)" % (ft["ask_id"], got))
        else:
            s -= 1.0
            notes.append("FIDELITY ERROR on %s: called it %r, it is %r — %s"
                         % (ft["ask_id"], got, ft["correct_fidelity"], ft["why"][:70]))
    return round(max(0.0, min(float(W_HONESTY), s)), 2)


def judges_checklist(rep) -> List[str]:
    """Not scored. What is objectively present, so three judges score the same work
    the same way."""
    out = []
    regs = [f for f in rep.get("findings", []) if f.get("is_regression")]
    dismissed = [f for f in rep.get("findings", []) if not f.get("is_regression")
                 and f.get("not_a_regression_because")]
    n = len(regs)
    imp = lambda f: f.get("impact") or {}
    with_impact = [f for f in regs if imp(f).get("conversations_affected") is not None]
    derived = [f for f in with_impact if imp(f).get("derivation")]
    pres = rep.get("prescriptions", [])
    with_dec = [p for p in pres if p.get("decision")]
    approved = [p for p in pres if (p.get("approval") or {}).get("verdict")]

    out.append("regressions reported ......... %d   (dismissed as non-regressions: %d)"
               % (n, len(dismissed)))
    out.append("  carrying an impact block ... %d/%d" % (len(with_impact), n))
    out.append("  impact shows its working ... %d/%d   <- traceable, not asserted" % (len(derived), n))
    out.append("  naming who must act ........ %d/%d" % (len([f for f in regs if f.get("audience")]), n))
    out.append("  costing inaction ........... %d/%d" % (len([f for f in regs if f.get("if_nothing_changes")]), n))
    out.append("prescriptions ................ %d" % len(pres))
    out.append("  framed as a decision ....... %d/%d" % (len(with_dec), len(pres)))
    out.append("  with a human verdict ....... %d/%d   <- the approval gate is real"
               % (len(approved), len(pres)))
    for f in with_impact:
        sh = imp(f).get("share_of_traffic")
        if sh is not None and (sh <= 0 or sh > 0.9):
            out.append("  ? %s claims %.0f%% of traffic affected — check that" % (f["id"], sh * 100))
        if imp(f).get("conversations_affected", 0) <= 0:
            out.append("  ? %s is a regression affecting no conversations" % f["id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    a = ap.parse_args()

    rep, gt = load(a.report), load(a.ground_truth)
    sections = []
    out = {}
    for label, fn, weight in (
        ("Diagnostic accuracy", score_accuracy, W_ACCURACY),
        ("Specificity", score_specificity, W_SPECIFICITY),
        ("Loop completeness", score_loop, W_LOOP),
        ("Honesty", score_honesty, W_HONESTY),
    ):
        notes: List[str] = []
        val = fn(rep, gt, notes)
        out[label] = {"score": val, "max": weight, "notes": notes}
        sections.append((label, val, weight, notes))

    machine = sum(s for _, s, _, _ in sections)
    if a.json:
        print(json.dumps({"team": rep.get("team"), "corpus": rep.get("corpus"),
                          "machine_score": round(machine, 2), "machine_max": W_MACHINE,
                          "human_slot": W_HUMAN, "sections": out,
                          "judges_checklist": judges_checklist(rep)}, indent=2))
        return 0

    print("=" * 74)
    print("NEXUS LOOP  ·  scorecard")
    print("  team    %s" % rep.get("team"))
    print("  corpus  %s   (ground truth: variant %s)" % (rep.get("corpus"), gt["variant"]))
    print("=" * 74)
    for label, val, weight, notes in sections:
        bar = "#" * int(round(24 * val / weight)) + "." * (24 - int(round(24 * val / weight)))
        print("\n%-22s %5.1f / %-3d  [%s]" % (label, val, weight, bar))
        for n in notes:
            print("     %s" % n)
    print("\n" + "-" * 74)
    print("%-22s %5.1f / %-3d  (machine-scored)" % ("SUBTOTAL", machine, W_MACHINE))
    print("-" * 74)
    print("\nJUDGES' CHECKLIST — not scored here. The 45 human points are for the QUALITY")
    print("of what follows, not its presence.\n")
    for line in judges_checklist(rep):
        print("     " + line)
    print("""
     Scored by people:
       Decision quality  18   is each finding something an operator could act on,
                              with its impact traceable to the corpus
       The screen        15   can a stranger read a finding, see the evidence, act
       Framing           12   did they show the refusal, state coverage, admit the fakes
""")
    print("-" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
