#!/usr/bin/env python3
"""Start the engine and the screen together, then show what your decisions change.

    python3 run.py

Builds loop-report.json if it is missing, serves the screen, opens it, and then watches
the report file. Every time you approve, reject or defer something on screen, this prints
what changed in the file and what it did to the judges' checklist — so the write-back is
visible instead of being something you have to take on trust.

    python3 run.py --rebuild     rebuild the report first (this clears recorded decisions)
    python3 run.py --unknown     build and serve a corpus with an unclassified fault in it
    python3 run.py --sample      build from the 5% sample (quick; spot-checks only)
    python3 run.py --port 9000   serve somewhere else

Ctrl-C stops the server.

Why rebuilding clears decisions: an approval is bound to the report's content hash, so it
is a decision about the exact evidence someone read. Regenerating the report produces a
new hash, and a decision attached to evidence that no longer exists would be a lie. So
this script does NOT rebuild by default — your decisions survive a restart.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(ROOT, "nexus-loop-day1", "kit")
SCORER = os.path.join(ROOT, "nexus-loop-day1", "tools", "nexus-loop-kit", "score.py")
GROUND_TRUTH = os.path.join(KIT, "ground_truth", "ground_truth.json")
PY = sys.executable or "python3"


def build(out_path, sample=False):
    cmd = [PY, "-m", "engine.cli", "--kit", KIT, "--team", "solo", "--out", out_path]
    if sample:
        cmd.append("--sample")
    print("building the report…")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit("the engine did not produce a report; nothing to serve")


def build_unknown(out_path):
    """A corpus with a confirmed regression the engine cannot classify.

    The practice corpus never produces one, but the sealed corpus can, so this is the only
    way to see that path on screen: it skips the cause and the fix and asks you for your
    own reading instead. The transform is the one documented in UNKNOWN_CAUSE_CLASS.md —
    flip one healthy cohort's outcomes halfway through and re-run the engine over it.
    """
    from collections import Counter
    sys.path.insert(0, ROOT)
    from engine import cli, io_corpus, paths

    print("building a corpus with an unclassified fault (this takes a minute)…")
    corpus = paths.corpus_dir(KIT, sample=False)
    catalog, labels = paths.load_catalog(KIT), io_corpus.read_labels(KIT)
    sessions = io_corpus.read_sessions(corpus)
    steps = io_corpus.collect_steps(corpus, ("tool_call", "kb_lookup"))
    config_rows = io_corpus.read_config_timeline(corpus)

    def run(rows):
        return cli.compute_report("solo", "A", catalog, rows, steps["tool_call"],
                                  steps["kb_lookup"], config_rows, labels, rows)

    base = run(sessions)
    fmap = {f["id"]: f for f in base["findings"]}
    taken = {(fmap[d["finding_id"]]["tenant"], fmap[d["finding_id"]]["cohort"].get("intent"))
             for d in base["diagnoses"] if fmap[d["finding_id"]].get("is_regression")}
    cohort = None
    for (tenant, intent), n in Counter((s["tenant"], s["intent"]) for s in sessions).most_common():
        rows = [s for s in sessions if s["tenant"] == tenant and s["intent"] == intent]
        resolved = sum(1 for s in rows if s["session_end"] == "resolved") / len(rows)
        if (tenant, intent) not in taken and n >= 300 and resolved >= 0.6:
            cohort = (tenant, intent, rows)
            break
    if cohort is None:
        sys.exit("could not find a healthy cohort to break; run without --unknown")
    tenant, intent, rows = cohort
    cut = sorted(s["day"] for s in rows)[len(rows) // 2]

    flipped = []
    for s in sessions:
        row = dict(s)
        if (row["tenant"] == tenant and row["intent"] == intent
                and row["day"] >= cut and row["session_end"] == "resolved"):
            row["session_end"] = "handoff"
        flipped.append(row)

    with open(out_path, "w") as fh:
        json.dump(run(flipped), fh, indent=2)
    print("broke %s / %s from day %d onward" % (tenant, intent, cut))


def show_path(path):
    """Repo-relative when it lives here, absolute when it does not."""
    rel = os.path.relpath(path, ROOT)
    return path if rel.startswith("..") else rel


def approvals_of(report):
    out = {}
    for p in report.get("prescriptions", []):
        approval = p.get("approval")
        if approval:
            out[p["id"]] = dict(approval, _history=len(p.get("approval_history", [])))
    return out


def read_report(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def verdict_count(path):
    """The judges' checklist line for decisions, straight from the organisers' scorer."""
    if not (os.path.exists(SCORER) and os.path.exists(GROUND_TRUTH)):
        return None
    try:
        out = subprocess.run([PY, SCORER, "--report", path, "--ground-truth", GROUND_TRUTH],
                             cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "human verdict" in line:
            return line.strip().split("<-")[0].strip()
    return None


def describe(pid, before, after):
    print("\n  %s  %s" % (time.strftime("%H:%M:%S"), pid))
    if before is None:
        print("    first decision on this fix")
    else:
        print("    was %s by %s" % (before.get("verdict"), before.get("decided_by")))
    print("    now %s by %s (confidence: %s)"
          % (after.get("verdict"), after.get("decided_by"), after.get("confidence")))
    print("    reason: %s" % after.get("reason"))
    print("    at: %s" % after.get("at"))
    if after.get("_history", 0) > 1:
        print("    %d decisions kept in approval_history; the earlier one is not overwritten"
              % after["_history"])


def watch(path, poll=0.7):
    seen = approvals_of(read_report(path) or {})
    if seen:
        print("\n%d decision(s) already recorded in this report." % len(seen))
    print("\nwatching %s — approve or reject something on screen and it shows up here.\n"
          % show_path(path))
    stamp = None
    while True:
        time.sleep(poll)
        try:
            now = os.path.getmtime(path)
        except OSError:
            continue
        if now == stamp:
            continue
        stamp = now
        report = read_report(path)
        if report is None:
            continue
        current = approvals_of(report)
        changed = False
        for pid, after in current.items():
            before = seen.get(pid)
            if before != after:
                describe(pid, before, after)
                changed = True
        for pid in set(seen) - set(current):
            print("\n  %s  %s — decision removed" % (time.strftime("%H:%M:%S"), pid))
            changed = True
        seen = current
        if changed:
            line = verdict_count(path)
            if line:
                print("    scorer now says: %s" % line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default=os.path.join(ROOT, "engine", "out", "loop-report.json"))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild the report first, clearing any recorded decisions")
    ap.add_argument("--sample", action="store_true", help="build from the 5%% sample corpus")
    ap.add_argument("--unknown", action="store_true",
                    help="serve a corpus containing an unclassified fault")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    a = ap.parse_args()

    report_path = os.path.abspath(a.report)
    if a.unknown:
        report_path = os.path.join(ROOT, "engine", "out", "loop-report-unknown.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    exists = os.path.exists(report_path)

    if a.rebuild and exists:
        recorded = len(approvals_of(read_report(report_path) or {}))
        if recorded:
            print("rebuilding will clear %d recorded decision(s), because they are bound to "
                  "the current evidence." % recorded)
            if input("continue? [y/N] ").strip().lower() not in ("y", "yes"):
                sys.exit("left the existing report alone")

    if a.unknown and (a.rebuild or not exists):
        build_unknown(report_path)
    elif a.rebuild or not exists:
        build(report_path, sample=a.sample)
    else:
        print("using the existing report at %s" % show_path(report_path))
        print("  (pass --rebuild to regenerate it; that clears recorded decisions)")

    url = "http://127.0.0.1:%d" % a.port
    server = subprocess.Popen(
        [PY, os.path.join(ROOT, "screen", "serve.py"),
         "--report", report_path, "--port", str(a.port)],
        cwd=ROOT)
    try:
        time.sleep(1.5)
        if server.poll() is not None:
            sys.exit("the screen server stopped immediately; is port %d already in use?" % a.port)

        report = read_report(report_path) or {}
        regressions = [f for f in report.get("findings", []) if f.get("is_regression")]
        print("\n" + "=" * 74)
        print("  Loop Desk   %s" % url)
        print("  report      %s" % show_path(report_path))
        print("  build       %s" % str(report.get("report_build", "n/a"))[7:19])
        print("=" * 74)
        print("  %d finding(s) needing a decision, %d set aside, %d question(s) refused"
              % (len(regressions),
                 len(report.get("findings", [])) - len(regressions),
                 len(report.get("gaps", []))))
        print("\n  things worth trying:")
        print("    - walk a fault through all six slides, then approve it")
        print("    - reject one with a reason, and watch it score the same as an approval")
        print("    - change a decision you already made; the first one is kept, not overwritten")
        print("    - click any bar or figure to see its definition, coverage and derivation")
        print("    - open a second terminal and rebuild the report while this page is open;")
        print("      the next decision is refused, because the evidence moved underneath it")
        if not a.no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        watch(report_path)
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
