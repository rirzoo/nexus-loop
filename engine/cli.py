"""Entrypoint: build a loop-report.json from a kit.

    python3 -m engine.cli --kit nexus-loop-day1/kit [--sample] --team solo \
                          --out engine/out/report.json
"""
from __future__ import annotations

import argparse
import os

from . import coverage, gaps, io_corpus, metrics, paths, report
from . import cohorts, standard, detect_faults, detect_decoys


def build_report(kit_dir: str, team: str, sample: bool = False) -> dict:
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

    # ---- metrics (honesty spine) ----
    # Calibration describes the judge globally and its human labels reference specific
    # session_ids that mostly fall outside the 5% sample, so it is always computed
    # against the full-corpus sessions, never the sample slice.
    if sample:
        cal_sessions = io_corpus.read_sessions(paths.corpus_dir(kit_dir, sample=False))
    else:
        cal_sessions = sessions
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
    findings, diagnoses = [], []
    std = standard.mine_standard(sessions)
    for detect in (detect_faults.detect_kb_gap,
                   detect_faults.detect_tool_contract_break,
                   detect_faults.detect_prompt_regression):
        fs, ds = detect(sessions, tool_calls, kb_lookups, config_rows, std)
        findings += fs
        diagnoses += ds
    for detect in (detect_decoys.detect_traffic_mix,
                   detect_decoys.detect_load_event,
                   detect_decoys.detect_judge_boundary):
        fs, ds = detect(sessions, tool_calls, kb_lookups, config_rows)
        findings += fs
        diagnoses += ds

    notes = ("Engine build. Metrics computed from data (coverage recomputed, never "
             "assumed). A11/A09 refused as gaps; A09 answered as a gap rather than a "
             "fabricated judged metric since the kit ships no abandonment-reason "
             "labels to calibrate against. C2 cardinality refusal is emitted for "
             "honesty even though score.py does not currently check it. Detection is "
             "signature-based over config_timeline + cohort structure, no hardcoded "
             "days/tenants. corpus=%s%s." % (manifest["corpus_variant"],
                                             " (5% sample)" if sample else ""))

    return report.assemble(team, manifest["corpus_variant"], m, g, findings,
                           diagnoses, standard=std, system_notes=notes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="nexus-loop-day1/kit")
    ap.add_argument("--team", default="solo")
    ap.add_argument("--out", default="engine/out/loop-report.json")
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()

    rep = build_report(a.kit, a.team, sample=a.sample)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    report.write(rep, a.out)
    print("wrote %s  (%d metrics, %d findings, %d diagnoses, %d gaps, %d standard)"
          % (a.out, len(rep["metrics"]), len(rep["findings"]),
             len(rep["diagnoses"]), len(rep["gaps"]), len(rep["standard"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
