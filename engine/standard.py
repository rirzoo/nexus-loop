"""Mine what 'good' looks like from the deployment's own traffic.

Rule 2: this only READS history to describe the standard; it never redefines a
metric. Per tenant, the standard for resolution_rate is the top-decile intent
(the `best`) and the median intent, over intents with enough volume to trust.
"""
from __future__ import annotations

from . import cohorts


def mine_standard(sessions, metric="resolution_rate") -> list:
    out = []
    for tenant, tsessions in cohorts.by_tenant(sessions).items():
        intents = cohorts.by_intent(tsessions)
        rates = {i: cohorts.resolution_rate(ss) for i, ss in intents.items()
                 if len(ss) >= cohorts.MIN_COHORT_SESSIONS}
        if len(rates) < 2:
            continue
        vals = list(rates.values())
        best = cohorts.percentile(vals, 0.9)
        med = cohorts.median(vals)
        out.append({
            "tenant": tenant,
            "cohort": {"scope": "intents", "tenant": tenant},
            "metric": metric,
            "exemplar_n": len(rates),
            "best": round(best, 4),
            "median": round(med, 4),
            "derivation": ("top-decile and median resolution_rate across %d %s intents "
                           "with >=%d sessions each, computed from the corpus"
                           % (len(rates), tenant, cohorts.MIN_COHORT_SESSIONS)),
        })
    return out


def tenant_standard(std, tenant) -> dict | None:
    for s in std:
        if s["tenant"] == tenant:
            return s
    return None


def noise_band(series_list) -> float:
    """The deployment's own week-to-week wobble for a metric: the median absolute
    change between consecutive weeks, pooled across stable cohorts. This is the
    precision floor verify.py does not give us — a candidate deviation must clear it,
    not just the release-gate magnitude. `series_list` is a list of {week: value}."""
    deltas = []
    for weekly in series_list:
        weeks = sorted(weekly)
        for j in range(1, len(weeks)):
            deltas.append(abs(weekly[weeks[j]] - weekly[weeks[j - 1]]))
    return cohorts.median(deltas) if deltas else 0.0
