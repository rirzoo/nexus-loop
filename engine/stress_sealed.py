#!/usr/bin/env python3
"""Score the engine against corpora shaped like the SEALED one, not like the practice one.

    python3 engine/stress_sealed.py --n 20

Why this exists
---------------
The practice corpus is one point in the sealed corpus's parameter space, and it is an
unrepresentative one. `nlkit.world.sealed_schedule(secret)` lays the three faults and three
decoys out from an organiser passphrase; sampling it shows the practice variant sits at the
far edge of every axis:

    kb_gap_day      practice 34   sealed median 10
    prompt_reg_day  practice 46   sealed median 19
    silent_tool_day practice 40   sealed median 15
    healthy days after a fault ends   practice 0-11   sealed 15-40

So in the practice corpus the faults run to the end of the corpus, and in the sealed corpus
they are episodes that recover. An engine validated only on variant A can score 55/55 there
and miss every fault on the real thing. This harness closes that gap: it builds corpora the
way the organisers will, runs the engine untouched, and scores them with the organisers'
own score.py.

Nothing is written to disk. Each corpus is generated straight into memory (sessions plus the
tool_call/kb_lookup steps the engine reads), so a sweep needs no `make kit` and leaves no
artefacts. Roughly 15s per layout.

Requires the kit generator at nexus-loop-day1/tools/nexus-loop-kit/ — it ships with the repo
but is git-ignored, so a fresh clone that dropped the kit will get a clear message instead of
a traceback.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
KIT_TOOLS = os.path.join(ROOT, "nexus-loop-day1", "tools", "nexus-loop-kit")
KIT = os.path.join(ROOT, "nexus-loop-day1", "kit")

# Put the repo root on the path and import the engine absolutely, so this runs both as
# `python3 engine/stress_sealed.py` and as `python3 -m engine.stress_sealed`. A relative
# import would work only under -m, and a pre-freeze check nobody can invoke the obvious
# way is a check nobody runs.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MAX = {"accuracy": 20, "specificity": 15, "loop": 8, "honesty": 12}


def _load_kit_tools():
    """Import the generator and the scorer, or explain what is missing and stop."""
    if not os.path.isdir(KIT_TOOLS):
        sys.exit("cannot find the kit generator at %s — this harness needs it to build "
                 "sealed-shaped corpora. Restore nexus-loop-day1/ and re-run." % KIT_TOOLS)
    if KIT_TOOLS not in sys.path:
        sys.path.insert(0, KIT_TOOLS)
    try:
        from nlkit import labels as labels_mod
        from nlkit import world
        from nlkit.simulate import Simulator
    except ImportError as e:
        sys.exit("kit generator present but not importable (%s)" % e)
    spec = importlib.util.spec_from_file_location(
        "nlkit_score", os.path.join(KIT_TOOLS, "score.py"))
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    return world, labels_mod, Simulator, scorer


def build_corpus(world, labels_mod, Simulator, schedule, label, seed=42):
    """Generate one corpus in memory. Mirrors what cli.build_report reads off disk:
    sessions, the tool_call and kb_lookup steps, config_timeline and the human labels.
    Transcript text and llm_call rows are dropped on the floor — the engine never reads
    them, and keeping them would triple the memory for nothing."""
    sessions, tool_calls, kb_lookups = [], [], []
    for sess, steps, _turns in Simulator(schedule, seed, 1.0, label=label).run():
        sessions.append(sess)
        for r in steps:
            if r["step_type"] == "tool_call":
                tool_calls.append(r)
            elif r["step_type"] == "kb_lookup":
                kb_lookups.append(r)
    config_rows = [{"day": c.day, "date": "", "tenant": c.tenant, "kind": c.kind,
                    "target": c.target, "from_value": c.from_value,
                    "to_value": c.to_value, "note": c.note}
                   for c in world.config_timeline(schedule)]
    lab = labels_mod.sample(sessions, seed)
    labels = {"outcome": lab["outcome_labels"], "rubric": lab["rubric_scores"]}
    return sessions, tool_calls, kb_lookups, config_rows, labels


def score_report(scorer, rep, gt):
    """Run the organisers' four scoring functions. Returns (total, {name: (score, notes)})."""
    out, total = {}, 0.0
    for name, fn in (("accuracy", scorer.score_accuracy),
                     ("specificity", scorer.score_specificity),
                     ("loop", scorer.score_loop),
                     ("honesty", scorer.score_honesty)):
        notes = []
        val = fn(rep, gt, notes)
        out[name] = (val, notes)
        total += val
    return round(total, 2), out


def run_layout(world, labels_mod, Simulator, scorer, catalog, secret, seed=42):
    """One sealed layout end to end: derive it, build it, run the engine, score it."""
    from engine import cli

    if secret == "A":                       # the practice layout, as a control
        schedule, label = world.FAULT_SCHEDULE["A"], "A"
    else:
        schedule, label = world.sealed_schedule(secret), "SEALED"
    corpus = build_corpus(world, labels_mod, Simulator, schedule, label, seed)
    rep = cli.compute_report("solo", label, catalog, *corpus, corpus[0])
    total, sections = score_report(scorer, rep, world.ground_truth(schedule, label))
    return schedule, rep, total, sections


def describe(schedule):
    v = schedule
    return ("kb=%d/%d prompt=%d/%d tool=%d/%d mix=%d spike=%d judge=%d"
            % (v["kb_gap_day"], v["kb_gap_len"], v["prompt_reg_day"], v["prompt_reg_len"],
               v["silent_tool_day"], v["silent_tool_len"], v["mix_shift_day"],
               v["volume_spike_day"], v["judge_day"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=5, help="how many sealed layouts to sweep")
    ap.add_argument("--prefix", default="stress",
                    help="passphrase prefix; layouts are <prefix>-0000, <prefix>-0001, ... "
                         "Change it to sweep a fresh, previously unseen set.")
    ap.add_argument("--secret", action="append", default=[],
                    help="score one specific passphrase (repeatable); skips --n/--prefix")
    ap.add_argument("--no-control", action="store_true",
                    help="skip the variant-A control run")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verbose", action="store_true",
                    help="print every scorer note, not just the failures")
    a = ap.parse_args()

    world, labels_mod, Simulator, scorer = _load_kit_tools()
    from engine import paths
    catalog = paths.load_catalog(KIT)

    secrets = a.secret or ["%s-%04d" % (a.prefix, i) for i in range(a.n)]
    if not a.no_control:
        secrets = ["A"] + secrets

    print("%-22s %-46s %6s %6s %6s %6s %8s"
          % ("layout", "schedule", "acc", "spec", "loop", "hon", "TOTAL"))
    print("-" * 104)
    totals = []
    for secret in secrets:
        schedule, rep, total, sections = run_layout(
            world, labels_mod, Simulator, scorer, catalog, secret, a.seed)
        print("%-22s %-46s %6.2f %6.2f %6.2f %6.2f %8.2f"
              % (secret[:22], describe(schedule),
                 sections["accuracy"][0], sections["specificity"][0],
                 sections["loop"][0], sections["honesty"][0], total))
        for name, (val, notes) in sections.items():
            for note in notes:
                if a.verbose or note.startswith(("MISS", "FALSE", "STRAY", "QUIET",
                                                 "FIDELITY", "MISSED")):
                    print("      %-12s %s" % (name, note))
        if secret != "A":
            totals.append(total)

    if totals:
        perfect = sum(1 for t in totals if t >= 55.0)
        print("-" * 104)
        print("sealed layouts n=%d   mean=%.2f   min=%.2f   max=%.2f   perfect=%d/%d"
              % (len(totals), statistics.mean(totals), min(totals), max(totals),
                 perfect, len(totals)))
        return 0 if perfect == len(totals) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
