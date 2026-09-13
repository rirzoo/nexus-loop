"""Streaming readers for the corpus. Read-only, stdlib only.

corpus_sample vs corpus is chosen by the caller via paths.corpus_dir; the schema
and joins are identical, so nothing here needs to know which one it's reading.
"""
from __future__ import annotations

import csv
import gzip
import json
import os


def stream_jsonl_gz(path: str):
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_jsonl_gz(path: str) -> list:
    return list(stream_jsonl_gz(path))


def read_sessions(corpus: str) -> list:
    return load_jsonl_gz(os.path.join(corpus, "sessions.jsonl.gz"))


def stream_steps(corpus: str):
    return stream_jsonl_gz(os.path.join(corpus, "agent_steps.jsonl.gz"))


def collect_steps(corpus: str, keep_types) -> dict:
    """One streaming pass over agent_steps, keeping only the step types asked for.
    Returns {step_type: [rows]}. tool_call (~62k) and kb_lookup (~72k) are small;
    the bulk (turn/llm_call) is skipped so the full corpus stays in memory cheaply."""
    keep = set(keep_types)
    out = {t: [] for t in keep}
    for r in stream_steps(corpus):
        st = r.get("step_type")
        if st in keep:
            out[st].append(r)
    return out


def read_config_timeline(corpus: str) -> list:
    """Each row: day(int), date, tenant, kind, target, from_value, to_value, note."""
    out = []
    with open(os.path.join(corpus, "config_timeline.csv"), newline="") as f:
        for row in csv.DictReader(f):
            row["day"] = int(row["day"])
            out.append(row)
    return out


def read_labels(kit_dir: str) -> dict:
    """Human labels used only for calibration and self-check, never as facts to
    report directly."""
    labels = os.path.join(kit_dir, "labels")
    out = {"outcome": [], "rubric": []}
    op = os.path.join(labels, "outcome_labels.jsonl")
    rp = os.path.join(labels, "rubric_scores.jsonl")
    if os.path.exists(op):
        with open(op) as f:
            out["outcome"] = [json.loads(x) for x in f if x.strip()]
    if os.path.exists(rp):
        with open(rp) as f:
            out["rubric"] = [json.loads(x) for x in f if x.strip()]
    return out
