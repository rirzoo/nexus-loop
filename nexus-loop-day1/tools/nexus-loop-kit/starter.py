#!/usr/bin/env python3
"""Day 1, hour 1. Run this before you write anything.

    python3 starter.py --kit ../../kit --team "your-team" --out my-loop-report.json
    python3 score.py --report my-loop-report.json --ground-truth ../../kit/ground_truth/ground_truth.json

It does three things and then gets out of your way:

  1. Prints an orientation summary of the corpus, so you don't spend a morning
     writing the same counting code every other team is writing.
  2. Authors TWO metrics properly — including computing coverage FROM THE DATA
     rather than assuming it — and writes a schema-valid loop report.
  3. Tells you what it deliberately did NOT do.

It finds nothing. It diagnoses nothing. It refuses nothing. Those are the
interesting parts and they are yours. What this removes is the undifferentiated
work of getting to a file the scorer will accept.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from collections import Counter, defaultdict


def stream(path):
    with gzip.open(path, "rt") as f:
        for line in f:
            yield json.loads(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="../../kit")
    ap.add_argument("--team", default="your-team")
    ap.add_argument("--out", default="my-loop-report.json")
    a = ap.parse_args()

    corpus = os.path.join(a.kit, "corpus")
    manifest = json.load(open(os.path.join(a.kit, "manifest.json")))

    # ---------------- 1 · orientation ------------------------------------
    sessions = list(stream(os.path.join(corpus, "sessions.jsonl.gz")))
    print("=" * 70)
    print("CORPUS  variant %s · %d days · %d sessions"
          % (manifest["corpus_variant"], manifest["days"], len(sessions)))
    print("=" * 70)

    by = lambda k: Counter(s[k] for s in sessions)
    for field in ("tenant", "agent_kind", "channel", "session_end", "judge_version"):
        top = ", ".join("%s=%d" % kv for kv in by(field).most_common(6))
        print("  %-14s %s" % (field, top))
    print("  %-14s %d distinct" % ("intent", len(by("intent"))))
    print("  %-14s %s" % ("step types", ", ".join(
        "%s=%d" % kv for kv in sorted(manifest["step_type_counts"].items()))))

    # ---------------- 2 · two metrics, with real coverage -----------------
    tenants = sorted({s["tenant"] for s in sessions})

    # (a) containment — every session can produce it, so coverage is 1.0
    contained = sum(1 for s in sessions
                    if s["session_end"] == "resolved"
                    or (s["session_end"] == "handoff" and s["handoff_by_design"]))
    containment = round(contained / len(sessions), 4)

    # (b) tool failure rate — v2_flow sessions emit NO tool_call rows, so they
    #     must leave the denominator. Coverage is measured, not assumed.
    per_tenant_cov = {
        t: round(sum(1 for s in sessions if s["tenant"] == t and s["agent_kind"] == "v3_agent")
                 / max(1, sum(1 for s in sessions if s["tenant"] == t)), 4)
        for t in tenants
    }

    # NOTE the shape here: coverage is a property of a SCOPE, not of a metric name.
    # A single blended figure across two tenants whose real coverage is 0.72 and 0.93
    # describes neither of them, so the metric is authored per tenant.
    calls = Counter()
    errors = Counter()
    err_classes = Counter()
    for r in stream(os.path.join(corpus, "agent_steps.jsonl.gz")):
        if r["step_type"] != "tool_call":
            continue
        calls[r["tenant"]] += 1
        if r["outcome"] != "ok":
            errors[r["tenant"]] += 1
            err_classes[r["error_class"]] += 1
    tool_fail = {t: round(errors[t] / max(1, calls[t]), 4) for t in tenants}

    print("-" * 70)
    print("  containment_rate      %.4f   fidelity=measured  coverage=1.00" % containment)
    for t in tenants:
        print("  tool_failure_rate     %.4f   fidelity=measured  coverage=%.2f   [%s]"
              % (tool_fail[t], per_tenant_cov[t], t))
    print("     error_class mix     %s" % dict(err_classes.most_common(4)))

    report = {
        "team": a.team,
        "corpus": manifest["corpus_variant"],
        "system_notes": "Produced by starter.py. Two metrics, no analysis yet.",
        "metrics": [
            {
                "id": "m_containment", "name": "Containment rate", "ask_id": "A01",
                "grain": "session", "fidelity": "measured",
                "coverage": {"value": 1.0,
                             "basis": "session_end and handoff_by_design are present on every "
                                      "session, v2_flow and v3_agent alike"},
                "calibration": None,
                "plan": {
                    "source": "corpus/sessions.jsonl.gz",
                    "filter": "session_end = 'resolved' OR (session_end = 'handoff' "
                              "AND handoff_by_design = true)",
                    "denominator": "all sessions in scope",
                    "breakdowns": ["tenant", "intent", "channel", "agent_kind"],
                },
            },
        ] + [
            {
                "id": "m_tool_failure_" + t.split("-")[0],
                "name": "Tool failure rate — " + t, "ask_id": "A04",
                "grain": "step", "fidelity": "measured",
                "coverage": {"value": per_tenant_cov[t],
                             "basis": "v2_flow sessions emit no tool_call rows, so they are "
                                      "excluded from the denominator rather than counted as "
                                      "zero-error. Measured from this tenant's own traffic, "
                                      "not assumed.",
                             "excluded": ["agent_kind = v2_flow"]},
                "calibration": None,
                "plan": {
                    "source": "corpus/agent_steps.jsonl.gz WHERE step_type = 'tool_call'",
                    "filter": "outcome IN ('error','timeout') AND tenant = '%s'" % t,
                    "denominator": "all tool_call steps for this tenant (v3_agent only)",
                    "breakdowns": ["error_class", "tool_name", "channel"],
                },
            } for t in tenants
        ],
        "standard": [],
        "findings": [],
        "diagnoses": [],
        "prescriptions": [],
        "verifications": [],
        "gaps": [],
        "self_assessment": {"cycles": 0, "prescription_accuracy": {}},
    }
    with open(a.out, "w") as f:
        json.dump(report, f, indent=2)

    # ---------------- 3 · what it did not do ------------------------------
    print("-" * 70)
    print("wrote %s — schema-valid, and it will score." % a.out)
    print("""
It deliberately did NOT:
  · look for anything that went wrong          -> findings   (worth 25 + 20)
  · work out what "good" looks like here       -> standard
  · explain any cause                          -> diagnoses
  · read asks.md and decide what is answerable -> gaps       (worth real points)
  · publish a calibration for a judged metric  -> metrics[].calibration

Next: score it, then pick ONE of those and make the number move.

  python3 score.py --report %s \\
                   --ground-truth %s/ground_truth/ground_truth.json
""" % (a.out, a.kit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
