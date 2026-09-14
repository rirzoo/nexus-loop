"""The return arrow: the loop scoring its own predictions against outcomes.

Honest by construction — a prescription's accuracy can only be known once its predicted
delta has been checked against a real replay outcome. So `self_assess` reports real
per-change_type accuracy ONLY when verifications exist; with none, it returns a valid but
empty return arrow that says the arrow opens after the replay stage, rather than
fabricating a hit rate. No LLM, no network — arithmetic over the report's own fields.
"""
from __future__ import annotations

from collections import defaultdict

HIT_RATE_DOWNWEIGHT = 0.5   # a change type we get right less than half the time


def self_assess(prescriptions, verifications=None):
    """Aggregate predicted-vs-observed across verified prescriptions, keyed by change_type.

    hit_rate            = share of that change type whose replay verdict was 'improved'
    mean_prediction_error = mean |predicted delta - observed delta| over verified ones
    cycles              = number of verified prescriptions (predict -> check loops closed)
    downweighted        = change types the loop now trusts less (low hit rate)
    """
    verifications = verifications or []
    pmap = {p["id"]: p for p in prescriptions}
    vlink = [v for v in verifications if v.get("prescription_id") in pmap]
    if not vlink:
        return {
            "cycles": 0,
            "prescription_accuracy": {},
            "notes": ("Return arrow opens after the replay stage: prescription accuracy "
                      "is only claimed once a predicted delta is checked against a real "
                      "replay outcome (none run yet)."),
        }

    by_type = defaultdict(lambda: {"n": 0, "improved": 0, "errs": []})
    for v in vlink:
        p = pmap[v["prescription_id"]]
        ct = p["change_type"]
        b = by_type[ct]
        b["n"] += 1
        if v.get("verdict") == "improved":
            b["improved"] += 1
        pe = v.get("prediction_error")
        if pe is not None:
            b["errs"].append(abs(float(pe)))

    accuracy, downweighted = {}, []
    for ct, b in by_type.items():
        hit = b["improved"] / b["n"] if b["n"] else 0.0
        mpe = round(sum(b["errs"]) / len(b["errs"]), 4) if b["errs"] else None
        accuracy[ct] = {"n": b["n"], "hit_rate": round(hit, 4),
                        "mean_prediction_error": mpe}
        if hit < HIT_RATE_DOWNWEIGHT:
            downweighted.append(ct)

    out = {
        "cycles": len(vlink),
        "prescription_accuracy": accuracy,
        "notes": ("Each cycle predicted a delta at prescribe time and checked it against "
                  "the replay outcome; change types below a %.0f%% hit rate are "
                  "downweighted." % (HIT_RATE_DOWNWEIGHT * 100)),
    }
    if downweighted:
        out["downweighted"] = downweighted
    return out
