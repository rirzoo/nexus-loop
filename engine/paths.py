"""Locate and load the kit's static files. Read-only."""
from __future__ import annotations

import json
import os


def load_manifest(kit_dir: str) -> dict:
    with open(os.path.join(kit_dir, "manifest.json")) as f:
        return json.load(f)


def load_catalog(kit_dir: str) -> dict:
    with open(os.path.join(kit_dir, "catalog.json")) as f:
        return json.load(f)


def load_ground_truth(kit_dir: str) -> dict:
    """Practice-corpus answer key. Self-check only — never a detection input.
    Absent on the sealed run, so callers must tolerate None."""
    p = os.path.join(kit_dir, "ground_truth", "ground_truth.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def corpus_dir(kit_dir: str, sample: bool = False) -> str:
    return os.path.join(kit_dir, "corpus_sample" if sample else "corpus")


def capability(catalog: dict, cap_id: str) -> dict:
    for c in catalog.get("capabilities", []):
        if c.get("id") == cap_id:
            return c
    raise KeyError("no capability %r in catalog" % cap_id)
