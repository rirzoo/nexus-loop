"""Entrypoint: build a loop-report.json from a kit.

    python3 -m engine.cli --kit nexus-loop-day1/kit [--sample] --team solo \
                          --out engine/out/report.json
"""
from __future__ import annotations

import argparse
import os
import sys

from . import coverage, gaps, io_corpus, metrics, paths, preflight, report
from . import cohorts, standard, detect_faults, detect_decoys, prescribe, selfassess
from . import narrate


def build_report(kit_dir: str, team: str, sample: bool = False, warnings=None) -> dict:
    """Load a kit and build the report. Loading is separated from computing so the
    hardening harness can drive compute_report on transformed in-memory corpora
    (the sealed run's day/tenant/slice shifts) without rewriting the gz files."""
    catalog = paths.load_catalog(kit_dir)
    manifest = paths.load_manifest(kit_dir)
    corpus = paths.corpus_dir(kit_dir, sample=sample)

    sessions = io_corpus.read_sessions(corpus)
    step_subsets = io_corpus.collect_steps(corpus, ("tool_call", "kb_lookup"))
    tool_calls = step_subsets["tool_call"]
    kb_lookups = step_subsets["kb_lookup"]
    # config_timeline is a static file shared by both corpora; the sample slice does
    # not duplicate it, so always read the canonical one.
    config_rows = io_corpus.read_config_timeline(paths.corpus_dir(kit_dir, sample=False))
    labels = io_corpus.read_labels(kit_dir)

    # Calibration describes the judge globally and its human labels reference specific
    # session_ids that mostly fall outside the 5% sample, so it is always computed
    # against the full-corpus sessions, never the sample slice.
    cal_sessions = (io_corpus.read_sessions(paths.corpus_dir(kit_dir, sample=False))
                    if sample else sessions)
    suffix = " (5% sample)" if sample else ""
    return compute_report(team, manifest["corpus_variant"], catalog, sessions,
                          tool_calls, kb_lookups, config_rows, labels, cal_sessions,
                          notes_suffix=suffix, warnings=warnings)


def compute_report(team, corpus_variant, catalog, sessions, tool_calls, kb_lookups,
                   config_rows, labels, cal_sessions, notes_suffix="", warnings=None) -> dict:
    """Pure compute over already-loaded (and possibly transformed) data."""
    # ---- metrics (honesty spine) ----
    calibration = metrics.calibrate_quality(cal_sessions, labels["rubric"])
    m = metrics.author_metrics(sessions, tool_calls, kb_lookups, calibration)

    # ---- gaps: A11 refusal, A09 refusal, C2 cardinality refusal ----
    g = [gaps.gap_a11_failover(catalog), gaps.gap_a09_abandon_reason(catalog)]
    cust_distinct = coverage.distinct_count(
        sessions, lambda s: (s.get("custom_dims") or {}).get("customer_ref"))
    c2 = gaps.refuse_if_over_budget(
        "session.custom_dims.customer_ref", cust_distinct, catalog, ask_id="A10")
    if c2:
        g.append(c2)

    # ---- detection (findings + diagnoses) ----
    # Anomaly-first: cohorts are enumerated from data and change-points detected from
    # each cohort's own series; config_timeline is used only to attribute. The classified
    # detectors run first; detect_unexplained is the open-world safety net for cohorts
    # none of them explained (dedup: skip cohorts already covered by a classified finding).
    findings, diagnoses = [], []
    std = standard.mine_standard(sessions)
    for detect in (detect_faults.detect_kb_gap,
                   detect_faults.detect_tool_contract_break,
                   detect_faults.detect_prompt_regression):
        fs, ds = detect(sessions, tool_calls, kb_lookups, config_rows, std)
        findings += fs
        diagnoses += ds
    covered = {(f["tenant"], f["cohort"].get("intent")) for f in findings
               if f["cohort"].get("intent")}
    fs, ds = detect_faults.detect_unexplained(
        sessions, tool_calls, kb_lookups, config_rows, std, covered)
    findings += fs
    diagnoses += ds
    # Every cohort now carrying a regression, including the ones the safety net just
    # found. The mix-shift test needs them so it does not read a real fault elsewhere in
    # the tenant as a per-cohort rate move.
    covered |= {(f["tenant"], f["cohort"].get("intent")) for f in findings
                if f["cohort"].get("intent")}
    fs, ds = detect_decoys.detect_traffic_mix(
        sessions, tool_calls, kb_lookups, config_rows, covered)
    findings += fs
    diagnoses += ds
    for detect in (detect_decoys.detect_load_event,
                   detect_decoys.detect_judge_boundary):
        fs, ds = detect(sessions, tool_calls, kb_lookups, config_rows)
        findings += fs
        diagnoses += ds

    # ---- prescribe (deterministic: cause_class -> direct fix, reusing detection's
    # numbers as the predicted delta) + the return arrow (populated for real once the
    # replay stage writes verifications back; empty-but-honest until then). ----
    prescriptions = prescribe.prescribe(findings, diagnoses)
    self_assessment = selfassess.self_assess(prescriptions)

    notes = ("Engine build. Metrics computed from data (coverage recomputed, never "
             "assumed). A11/A09 refused as gaps; A09 answered as a gap rather than a "
             "fabricated judged metric since the kit ships no abandonment-reason "
             "labels to calibrate against. C2 cardinality refusal is emitted for "
             "honesty even though score.py does not currently check it. Detection is "
             "anomaly-first: cohorts and change-points come from data, config_timeline "
             "is used only to attribute, no hardcoded days/tenants. corpus=%s%s."
             % (corpus_variant, notes_suffix))
    warn_note = preflight.summarize_warnings(warnings or [])
    if warn_note:
        notes = (notes + " " + warn_note).strip()

    # ---- narration: the same facts, said in a way a stranger can act on. Runs before
    # assembly so the wording is covered by report_build; see engine/narrate.py. ----
    narration = narrate.annotate(findings, diagnoses, prescriptions, g, m, std,
                                 self_assessment)

    return report.assemble(team, corpus_variant, m, g, findings,
                           diagnoses, standard=std, system_notes=notes,
                           prescriptions=prescriptions,
                           self_assessment=self_assessment,
                           summary_plain=narration["summary_plain"],
                           whats_real=narration["whats_real"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="nexus-loop-day1/kit")
    ap.add_argument("--team", default="solo")
    ap.add_argument("--out", default="engine/out/loop-report.json")
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()

    # Preflight: validate the (possibly sealed) kit before reading it for real. Fatal
    # issues stop the run with a clear diagnostic instead of a mid-run stack trace;
    # warnings are surfaced and the engine degrades around them.
    fatal, warnings = preflight.validate_kit(a.kit, sample=a.sample)
    for w in warnings:
        print("preflight WARNING: " + w, file=sys.stderr)
    if fatal:
        print("preflight FATAL — cannot produce a valid report:", file=sys.stderr)
        for f in fatal:
            print("  x " + f, file=sys.stderr)
        return 2

    rep = build_report(a.kit, a.team, sample=a.sample, warnings=warnings)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    report.write(rep, a.out)
    print("wrote %s  (%d metrics, %d findings, %d diagnoses, %d gaps, %d standard)"
          % (a.out, len(rep["metrics"]), len(rep["findings"]),
             len(rep["diagnoses"]), len(rep["gaps"]), len(rep["standard"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
