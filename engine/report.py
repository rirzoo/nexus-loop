"""Assemble and write a schema-valid loop-report.json."""
from __future__ import annotations

import datetime
import hashlib
import json


def assemble(team, corpus_variant, metrics, gaps, findings, diagnoses,
             standard=None, system_notes="", prescriptions=None, verifications=None,
             self_assessment=None, summary_plain=None, whats_real=None) -> dict:
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

    rep = {
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
        # The plain-language layer the screen reads. It sits inside the hash below on
        # purpose: an approval is bound to the words the person read, not just the numbers.
        "summary_plain": summary_plain or "",
        "whats_real": whats_real or [],
    }
    # report_build: a content hash the screen binds a human's APPROVE/REJECT to, so a
    # decision made against evidence that was since regenerated is refused rather than
    # silently mis-attributed. Purely additive — it is derived from the report already
    # built above and touches no metric. generated_at is excluded so the id tracks the
    # evidence, not the wall clock: a byte-identical rebuild keeps the same id.
    core = {k: rep[k] for k in rep if k != "generated_at"}
    rep["report_build"] = "sha256:" + hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return rep


def write(report: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
