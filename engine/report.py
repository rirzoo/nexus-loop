"""Assemble and write a schema-valid loop-report.json."""
from __future__ import annotations

import datetime
import json


def assemble(team, corpus_variant, metrics, gaps, findings, diagnoses,
             standard=None, system_notes="", prescriptions=None, verifications=None,
             self_assessment=None) -> dict:
    # Guard: finding ids must be unique, and every diagnosis must point at a real
    # finding (the scorer only credits linked diagnoses).
    fids = [f["id"] for f in findings]
    assert len(fids) == len(set(fids)), "duplicate finding ids: %s" % fids
    fidset = set(fids)
    for d in diagnoses:
        assert d["finding_id"] in fidset, (
            "diagnosis %s references missing finding %s" % (d["id"], d["finding_id"]))
    # Guard: every prescription must link a real diagnosis (the scorer only credits
    # linked prescriptions), so a broken link fails loudly here rather than scoring 0.
    prescriptions = prescriptions or []
    dids = {d["id"] for d in diagnoses}
    for p in prescriptions:
        assert p["diagnosis_id"] in dids, (
            "prescription %s references missing diagnosis %s" % (p["id"], p["diagnosis_id"]))

    return {
        "team": team,
        "corpus": corpus_variant,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system_notes": system_notes,
        "metrics": metrics,
        "standard": standard or [],
        "findings": findings,
        "diagnoses": diagnoses,
        "prescriptions": prescriptions,
        "verifications": verifications or [],
        "gaps": gaps,
        "self_assessment": self_assessment or {"cycles": 0, "prescription_accuracy": {}},
    }


def write(report: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
