"""Verify prescriptions against the replay endpoint and write the results back.

    python3 -m engine.replay.client --report engine/out/report.json \
        --endpoint http://127.0.0.1:8719 --team solo [--kit nexus-loop-day1/kit] [--dry-run]

For each real prescription it POSTs the proposed change + the affected cohort + a golden
set of known-good sessions, reads the `rp_…` outcome, and records a verification with the
prediction error (predicted delta minus observed delta). Then it recomputes the return
arrow (self_assessment) from those verifications.

Deliberately degradable: if the endpoint is unreachable or the run budget is spent, it
skips cleanly, leaves verifications as they were, and never crashes the report — the
sealed-run endpoint may only appear at the day-6 freeze. `--dry-run` prints the request
bodies without calling anything.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

from .. import io_corpus, paths, report, selfassess

GOLDEN_N = 20        # a small known-good set; the endpoint only needs it to guard regressions
NO_FIX = "no_action"


def _golden_set(kit_dir, tenant, from_day):
    """Known-good session ids: resolved sessions in this tenant BEFORE the fault window
    (the pre-regression period is the natural golden set). Best-effort; [] if unavailable."""
    try:
        corpus = paths.corpus_dir(kit_dir, sample=False)
        sess = io_corpus.read_sessions(corpus)
    except Exception:
        return []
    good = [s["session_id"] for s in sess
            if s["tenant"] == tenant and s["day"] < from_day
            and s.get("session_end") == "resolved" and s.get("session_id")]
    return good[:GOLDEN_N]


def _post(endpoint, body, timeout=10):
    data = json.dumps(body).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/replay", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: guarded, replay only
        return json.loads(r.read())


def verify(report_path, endpoint, team, kit_dir=None, dry_run=False):
    rep = json.load(open(report_path))
    fmap = {f["id"]: f for f in rep.get("findings", [])}
    dmap = {d["id"]: d for d in rep.get("diagnoses", [])}

    verifications = []
    for p in rep.get("prescriptions", []):
        if p["change_type"] == NO_FIX:
            continue
        d = dmap.get(p["diagnosis_id"])
        f = fmap.get(d["finding_id"]) if d else None
        if not f:
            continue
        w = f.get("window") or {}
        cohort = dict(f.get("cohort") or {})
        cohort["from_day"] = w.get("from_day")
        cohort["to_day"] = w.get("to_day")
        body = {
            "team": team,
            "tenant": f["tenant"],
            "change": {"type": p["change_type"], "target": p["target"],
                       "description": p.get("description", "")},
            "cohort": cohort,
            "golden_set": _golden_set(kit_dir, f["tenant"], w.get("from_day", 0)) if kit_dir else [],
        }
        if dry_run:
            print(json.dumps(body, indent=2))
            continue
        try:
            res = _post(endpoint, body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print("replay unreachable (%s) — leaving verifications empty, marking stubbed" % e)
            return _finish(rep, report_path, verifications, stubbed=True)
        if res.get("error"):
            print("replay declined: %s (%s)" % (res.get("error"), res.get("hint", "")))
            break   # budget exhausted or bad request — stop, keep what we have
        pred = p.get("predicted_delta") or {}
        pred_delta = (pred.get("to") or 0) - (pred.get("from") or 0)
        obs_delta = res.get("delta")
        verifications.append({
            "prescription_id": p["id"],
            "replay_run_id": res.get("run_id"),
            "metric": res.get("metric"),
            "before": res.get("before"),
            "after": res.get("after"),
            "verdict": res.get("verdict"),
            "golden_set_pass": (res.get("golden_set") or {}).get("pass"),
            "prediction_error": (round(pred_delta - obs_delta, 4)
                                 if obs_delta is not None else None),
        })
        print("verified %s: %s %s->%s (%s), rp=%s"
              % (p["id"], res.get("metric"), res.get("before"), res.get("after"),
                 res.get("verdict"), res.get("run_id")))

    if dry_run:
        return 0
    return _finish(rep, report_path, verifications, stubbed=False)


def _finish(rep, report_path, verifications, stubbed):
    if verifications:
        rep["verifications"] = verifications
        rep["self_assessment"] = selfassess.self_assess(rep.get("prescriptions", []),
                                                        verifications)
    elif stubbed:
        note = rep.get("system_notes", "")
        if "replay stubbed" not in note:
            rep["system_notes"] = (note + " Replay verification stubbed: endpoint "
                                   "unreachable at run time (predicted_delta stands).").strip()
    report.write(rep, report_path)
    print("wrote %s  (%d verifications)" % (report_path, len(verifications)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--endpoint", default="http://127.0.0.1:8719")
    ap.add_argument("--team", default="solo")
    ap.add_argument("--kit", default="nexus-loop-day1/kit",
                    help="kit dir for sampling a golden set; omit to send an empty one")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return verify(a.report, a.endpoint, a.team, kit_dir=a.kit, dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
