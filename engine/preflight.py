"""Sealed-run preflight: validate a kit before the engine reads it for real.

The sealed day-6 corpus must run untouched, one command — and a renamed or missing
column would otherwise surface as a deep stack trace mid-run (== zero output). This
module does a cheap, read-only pass FIRST and classifies what it finds:

  - fatal    : required data the engine cannot degrade around (a missing/unreadable
               sessions or agent_steps file, or a STRUCTURAL column the detectors
               hard-index). The run must stop with a clear diagnostic naming the file
               and column, rather than crash.
  - warning  : something the engine can degrade around and keep producing a valid,
               honest report — a missing OPTIONAL column (accessed via .get()), a
               missing config_timeline (attribution becomes null), missing labels
               (calibration becomes a gap), or malformed config day-rows (skipped).

It is pure local file I/O (os/gzip/csv/json) — no network, no model, no writes — so it
is Rule-1/Rule-2 safe and a no-op on the well-formed practice corpus. cli surfaces the
findings to stderr and folds warnings into the report's system_notes.
"""
from __future__ import annotations

import csv
import gzip
import json
import os

# Columns the detectors HARD-INDEX (r["x"]) — absence cannot be degraded around.
STRUCTURAL = {
    "sessions": ("session_id", "tenant", "day", "intent", "session_end", "turns",
                 "agent_id"),
    "tool_call": ("session_id", "tenant", "day", "tool_name", "outcome"),
    "kb_lookup": ("session_id", "tenant", "day", "intent"),
}
# Columns accessed via .get() — absence degrades to a null/zero, so only a warning.
OPTIONAL = {
    "sessions": ("cost_usd",),
    "tool_call": ("result_field_count", "retry_count", "response_bytes"),
    "kb_lookup": ("kb_hit", "kb_top_score"),
}
# config_timeline: structural (used to join/attribute) vs evidence-only (defaulted "").
CONFIG_STRUCTURAL = ("day", "tenant", "kind", "target")
CONFIG_EVIDENCE = ("from_value", "to_value", "note", "date")

SAMPLE_ROWS = 200  # enough to catch a globally-missing column cheaply


def _sample_jsonl_gz(path, n=SAMPLE_ROWS):
    rows = []
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if len(rows) >= n:
                break
    return rows


def _keys_present(rows, key):
    """A column counts as present if ANY sampled row carries it (rows are sparse: a
    step file mixes step_types, so a tool-only column is absent from turn rows)."""
    return any(key in r for r in rows)


def validate_kit(kit_dir, sample=False):
    """Return (fatal, warnings): two lists of human-readable strings. Empty fatal means
    the engine can produce a valid report (possibly degraded, per warnings)."""
    fatal, warnings = [], []
    corpus = os.path.join(kit_dir, "corpus_sample" if sample else "corpus")

    # ---- static kit files ----
    for name, path in (("manifest.json", os.path.join(kit_dir, "manifest.json")),
                       ("catalog.json", os.path.join(kit_dir, "catalog.json"))):
        if not os.path.exists(path):
            fatal.append("missing required file: %s" % path)
        else:
            try:
                with open(path) as fh:
                    json.load(fh)
            except Exception as e:  # noqa: report any parse failure, don't crash preflight
                fatal.append("%s is unreadable/invalid JSON: %s" % (name, e))

    # ---- sessions (fatal if missing/unreadable or a structural column is gone) ----
    sess_path = os.path.join(corpus, "sessions.jsonl.gz")
    if not os.path.exists(sess_path):
        fatal.append("missing required file: %s" % sess_path)
    else:
        try:
            srows = _sample_jsonl_gz(sess_path)
        except Exception as e:  # noqa
            fatal.append("%s is unreadable: %s" % (sess_path, e))
            srows = []
        if srows:
            for c in STRUCTURAL["sessions"]:
                if not _keys_present(srows, c):
                    fatal.append("sessions is missing structural column %r "
                                 "(detectors index it directly)" % c)
            for c in OPTIONAL["sessions"]:
                if not _keys_present(srows, c):
                    warnings.append("sessions is missing optional column %r "
                                    "(degrades to 0/None)" % c)
        elif os.path.exists(sess_path):
            fatal.append("sessions file has no rows")

    # ---- agent_steps: check the tool_call and kb_lookup step types we consume ----
    steps_path = os.path.join(corpus, "agent_steps.jsonl.gz")
    if not os.path.exists(steps_path):
        fatal.append("missing required file: %s" % steps_path)
    else:
        try:
            # sample more here: step types are interleaved, so we need enough rows to
            # see tool_call and kb_lookup at all.
            strows = _sample_jsonl_gz(steps_path, n=SAMPLE_ROWS * 20)
        except Exception as e:  # noqa
            fatal.append("%s is unreadable: %s" % (steps_path, e))
            strows = []
        for st in ("tool_call", "kb_lookup"):
            typed = [r for r in strows if r.get("step_type") == st]
            if not typed:
                warnings.append("no %r steps seen in the first %d rows — that detector "
                                "may have nothing to analyse" % (st, len(strows)))
                continue
            for c in STRUCTURAL[st]:
                if not _keys_present(typed, c):
                    fatal.append("%s steps missing structural column %r" % (st, c))
            for c in OPTIONAL[st]:
                if not _keys_present(typed, c):
                    warnings.append("%s steps missing optional column %r "
                                    "(degrades to 0/None)" % (st, c))

    # ---- config_timeline: degradable in full (attribution only) ----
    cfg_path = os.path.join(corpus, "config_timeline.csv")
    if not os.path.exists(cfg_path):
        warnings.append("no config_timeline.csv — findings still emit, but every "
                        "attribution will be null")
    else:
        try:
            with open(cfg_path, newline="") as f:
                reader = csv.DictReader(f)
                cols = set(reader.fieldnames or [])
                bad_days = 0
                for row in reader:
                    try:
                        int(row["day"])
                    except (TypeError, ValueError, KeyError):
                        bad_days += 1
            for c in CONFIG_STRUCTURAL:
                if c not in cols:
                    warnings.append("config_timeline missing column %r — attribution "
                                    "for affected rows will be degraded/null" % c)
            missing_ev = [c for c in CONFIG_EVIDENCE if c not in cols]
            if missing_ev:
                warnings.append("config_timeline missing evidence columns %s "
                                "(defaulted to empty)" % ", ".join(missing_ev))
            if bad_days:
                warnings.append("config_timeline has %d row(s) with a missing/non-int "
                                "day — skipped (not placed on the timeline)" % bad_days)
        except Exception as e:  # noqa
            warnings.append("config_timeline unreadable (%s) — attribution will be null" % e)

    # ---- labels: degradable (calibration becomes a gap) ----
    labels_dir = os.path.join(kit_dir, "labels")
    if not os.path.isdir(labels_dir):
        warnings.append("no labels/ dir — judged-metric calibration will be a gap")

    return fatal, warnings


def summarize_warnings(warnings):
    """A single-line note for the report's system_notes (schema-safe: reuses an
    existing field rather than adding a new section)."""
    if not warnings:
        return ""
    return ("Preflight noted %d schema inconsistency/ies (engine degraded around them): %s"
            % (len(warnings), " | ".join(warnings)))
