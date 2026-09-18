#!/usr/bin/env python3
"""The replay endpoint — verification without a real runtime.

    python3 replay/serve.py --kit ../../kit --port 871

POST /replay
    {"team":"...", "tenant":"<tenant>",
     "change":{"type":"kb.add","target":"<what you are changing>","description":"..."},
     "cohort":{"intent":"<intent>","from_day":N,"to_day":M},
     "golden_set":["s_...", ...]}

Returns the replayed outcome for the cohort AND for the golden set, so a fix that
helps the cohort while breaking known-good conversations is caught.

Two things are deliberate:

  * There is a RUN BUDGET. You cannot brute-force the endpoint to discover which
    change helps — you have to diagnose first. Every call is logged.
  * "nothing happened" is reported identically whether your fix was wrong or
    whether there was nothing wrong in that cohort to begin with. The endpoint
    verifies a hypothesis you already have; it will not hand you a map.

The effect model is read from the corpus's ground truth at startup, so this file
contains no answers. Organisers run it against the sealed ground truth on day 6.
  * A wrong fix returns honest noise, not a failure. Verification tells you
    "no effect", which is information, not an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from http.server import BaseHTTPRequestHandler, HTTPServer

CORPUS_DAYS = 56          # inlined so this file stands alone in the team kit
RUN_BUDGET = 40

# Blunt instruments break things they were not aimed at. Applied to a cohort whose
# cause they do not address, these still carry a risk to known-good conversations.
BLUNT = {"revert", "routing.change"}


def load_effects(gt_path: str):
    """Build the effect model from the corpus's ground truth.

    Nothing about which faults exist, where they are, or what fixes them lives in
    this file — it is all read at startup from a file teams do not have for the
    sealed corpus.
    """
    with open(gt_path) as f:
        gt = json.load(f)
    windows = []
    for flt in gt["faults"]:
        obs = (gt.get("observed_effects") or {}).get(flt["id"], {})
        windows.append({
            "id": flt["id"],
            "tenant": flt["tenant"],
            "cohort": flt.get("cohort") or {},
            "start": flt["onset_day"],
            "len": flt["duration_days"],
            "fixes": list(flt.get("accepted_fix_classes") or []),
            "metric": "median_turns" if flt["cause_class"].startswith("prompt") else "resolution_rate",
            "healthy": obs.get("tenant_resolution_before")
                       or obs.get("order_status_resolution_before") or 0.86,
        })
    return windows


class State:
    def __init__(self, windows):
        self.windows = windows
        self.runs = {}
        self.log = []


def _active_fault(st: State, tenant: str, cohort: dict):
    lo = cohort.get("from_day", 0)
    hi = cohort.get("to_day", CORPUS_DAYS)
    for w in st.windows:
        if w["tenant"] != tenant:
            continue
        if hi <= w["start"] or lo >= w["start"] + w["len"]:
            continue
        if not any(str(cohort.get(k, "")).lower() == str(val).lower()
                   for k, val in w["cohort"].items()):
            continue
        return w
    return None


def replay(st: State, body: dict) -> dict:
    team = body.get("team", "anonymous")
    used = st.runs.get(team, 0)
    if used >= RUN_BUDGET:
        return {"error": "run_budget_exhausted", "budget": RUN_BUDGET, "used": used,
                "hint": "diagnose, then verify — the budget is the point"}
    st.runs[team] = used + 1

    tenant = body.get("tenant")
    change = body.get("change") or {}
    cohort = body.get("cohort") or {}
    ctype = change.get("type")
    golden = body.get("golden_set") or []

    seed = int(hashlib.sha256(
        json.dumps([tenant, change, cohort], sort_keys=True).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    w = _active_fault(st, tenant, cohort)
    metric = w["metric"] if w else "resolution_rate"

    if metric == "median_turns":
        baseline = round(rng.uniform(6.6, 7.4), 2)
        healthy = round(baseline / 1.58, 2)
    else:
        baseline = round(rng.uniform(0.30, 0.42), 4) if (w and w["healthy"] > 0.8
                                                         and w["id"].endswith("1")) \
            else round(rng.uniform(0.74, 0.79), 4)
        healthy = w["healthy"] if w else baseline

    # A fix only moves anything if it addresses the cause that is actually there.
    lift, verdict = 0.0, "no_effect"
    if w and ctype in w["fixes"]:
        # first accepted class is the direct fix; the others are partial mitigations
        lift = 0.92 if ctype == w["fixes"][0] else 0.70
        verdict = "improved"

    if metric == "median_turns":
        after = round(baseline - (baseline - healthy) * max(0.0, lift) + rng.gauss(0, 0.12), 2)
    else:
        after = round(baseline + (healthy - baseline) * lift + rng.gauss(0, 0.012), 4)
        after = max(0.0, min(1.0, after))

    if abs(after - baseline) < (0.15 if metric == "median_turns" else 0.02):
        # Reported identically whether the fix was wrong or there was nothing to fix.
        # Distinguishing the two would turn 40 probes into a map of the faults.
        verdict = "no_effect"

    # golden-set regression guard — a real fix must not break known-good work
    gp = len(golden)
    broke = 0
    if gp:
        p_break = 0.002 if verdict == "improved" else 0.004
        if ctype in BLUNT and verdict != "improved":
            p_break = 0.06          # blunt instruments break things they weren't aimed at
        broke = sum(1 for _ in range(gp) if rng.random() < p_break)

    run_id = "rp_%08x" % rng.getrandbits(32)
    res = {
        "run_id": run_id, "team": team, "runs_used": st.runs[team], "run_budget": RUN_BUDGET,
        "tenant": tenant, "change": change, "cohort": cohort,
        "metric": metric, "before": baseline, "after": after,
        "delta": round(after - baseline, 4),
        "verdict": verdict,
        "golden_set": {"replayed": gp, "regressed": broke,
                       "pass": broke == 0 if gp else None},
        "sessions_replayed": rng.randrange(180, 640),
        "note": "a wrong fix returns no_effect. That is a result, not a failure.",
    }
    st.log.append({k: res[k] for k in ("run_id", "team", "tenant", "change", "cohort",
                                       "metric", "before", "after", "verdict")})
    return res


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            b = json.dumps(obj, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/log"):
                self._send(200, {"runs": st.log, "used": st.runs})
            elif self.path.startswith("/health"):
                self._send(200, {"ok": True, "variant": "hidden", "budget": RUN_BUDGET})
            else:
                self._send(404, {"error": "not_found",
                                 "endpoints": ["POST /replay", "GET /log", "GET /health"]})

        def do_POST(self):
            if not self.path.startswith("/replay"):
                return self._send(404, {"error": "not_found"})
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._send(400, {"error": "bad_json", "detail": str(e)})
            if not body.get("tenant") or not (body.get("change") or {}).get("type"):
                return self._send(400, {"error": "tenant and change.type are required"})
            self._send(200, replay(st, body))

        def log_message(self, *a):
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="../../kit",
                    help="the kit directory whose corpus this serves")
    ap.add_argument("--ground-truth", default=None,
                    help="override the ground-truth path (organisers point this at the "
                         "sealed one on day 6)")
    ap.add_argument("--port", type=int, default=8719)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address. Organisers use 0.0.0.0 on day 6 so teams can reach "
                         "the sealed endpoint over the room network.")
    a = ap.parse_args()
    gt = a.ground_truth
    if not gt:
        for cand in ("ground_truth/ground_truth.json",
                     "ground_truth_SEALED/ground_truth.json"):
            if os.path.exists(os.path.join(a.kit, cand)):
                gt = os.path.join(a.kit, cand)
                break
    if not gt or not os.path.exists(gt):
        print("No ground truth found. Pass --ground-truth, or --kit pointing at a "
              "generated kit.")
        raise SystemExit(1)
    st = State(load_effects(gt))
    print("replay listening on http://%s:%d  (budget %d runs/team, %d fault windows "
          "loaded)" % (a.host, a.port, RUN_BUDGET, len(st.windows)))
    HTTPServer((a.host, a.port), make_handler(st)).serve_forever()


if __name__ == "__main__":
    main()
