"""Human labels — the calibration set.

Deliberately noisy. Real human labelling agrees with itself ~92% of the time, so a
judged signal that reports 1.00 agreement has a bug, not a good judge. Teams that
publish a perfect calibration are reporting a mistake.
"""
from __future__ import annotations

import random
from typing import Dict, List

DISAGREE_BINARY = 0.075     # human vs. ground-truth outcome
RUBRIC_NOISE_SD = 0.62      # human vs. machine rubric score


def sample(sessions: List[dict], seed: int, n_binary: int = 400,
           n_rubric: int = 100) -> Dict[str, List[dict]]:
    rng = random.Random(seed * 31 + 17)

    # stratify by (tenant, week) so the set spans the whole corpus, not just the
    # busy weeks — otherwise calibration is measured on a biased slice.
    strata: Dict[tuple, List[dict]] = {}
    for s in sessions:
        strata.setdefault((s["tenant"], s["day"] // 7), []).append(s)
    keys = sorted(strata.keys())

    def draw(n: int) -> List[dict]:
        per = max(1, n // len(keys))
        out: List[dict] = []
        for k in keys:
            pool = strata[k]
            out.extend(rng.sample(pool, min(per, len(pool))))
        rng.shuffle(out)
        return out[:n]

    binary = []
    for s in draw(n_binary):
        resolved = s["session_end"] == "resolved"
        by_design = bool(s["handoff_by_design"]) if s["session_end"] == "handoff" else None
        if rng.random() < DISAGREE_BINARY:
            resolved = not resolved
        if by_design is not None and rng.random() < DISAGREE_BINARY:
            by_design = not by_design
        binary.append({
            "session_id": s["session_id"], "tenant": s["tenant"], "day": s["day"],
            "intent": s["intent"], "channel": s["channel"],
            "human_resolved": resolved,
            "human_handoff_by_design": by_design,
            "labeller": rng.choice(["h1", "h2", "h3", "h4"]),
        })

    rubric = []
    for s in draw(n_rubric):
        score = max(1.0, min(5.0, round(s["quality_score"] + rng.gauss(0, RUBRIC_NOISE_SD), 1)))
        rubric.append({
            "session_id": s["session_id"], "tenant": s["tenant"], "day": s["day"],
            "intent": s["intent"],
            "human_quality": score,
            "judge_version_at_label_time": s["judge_version"],
            "labeller": rng.choice(["h1", "h2", "h3"]),
            "note": "score this transcript 1-5 on whether the user's request was served",
        })
    return {"outcome_labels": binary, "rubric_scores": rubric}
