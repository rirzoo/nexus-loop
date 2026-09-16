"""Shared cohort/time primitives for detection. Read-only over in-memory rows."""
from __future__ import annotations

from collections import defaultdict

MIN_COHORT_SESSIONS = 30   # below this a cohort is too noisy to trust (esp. on sample)


def resolution_rate(sessions) -> float:
    if not sessions:
        return 0.0
    return sum(1 for s in sessions if s["session_end"] == "resolved") / len(sessions)


def in_window(rows, lo, hi, day_field="day"):
    """Rows with lo <= day <= hi."""
    return [r for r in rows if lo <= r[day_field] <= hi]


def first_appearance_day(rows, day_field="day") -> int | None:
    days = [r[day_field] for r in rows]
    return min(days) if days else None


def daily_counts(rows, day_field="day") -> dict:
    c = defaultdict(int)
    for r in rows:
        c[r[day_field]] += 1
    return dict(c)


def by_intent(sessions) -> dict:
    out = defaultdict(list)
    for s in sessions:
        out[s["intent"]].append(s)
    return dict(out)


def by_tenant(rows) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r["tenant"]].append(r)
    return dict(out)


def percentile(values, q) -> float:
    """q in [0,1]. Nearest-rank percentile; stdlib only."""
    if not values:
        return 0.0
    vs = sorted(values)
    idx = min(len(vs) - 1, max(0, int(round(q * (len(vs) - 1)))))
    return vs[idx]


def median(values) -> float:
    return percentile(values, 0.5)


def best_run(series, direction, min_len=1, min_baseline=1):
    """The contiguous run of buckets whose mean most differs, in `direction`, from the
    mean of the REST of the series.

    This is the engine's change-point primitive, and it looks for an EPISODE rather than a
    step. A planted fault begins, runs for a while, and the deployment recovers inside the
    corpus; a campaign surges and then stops. A before/after split-point search reads both
    backwards — the largest single level change in such a series is the *recovery* edge and
    its sign is inverted, so a directional test rejects the real event. Scanning runs also
    supplies a baseline when an event starts in the very first bucket and there is no
    before-period at all: the buckets outside the run are all the baseline there is.

    `series` is {bucket_key: value} — weeks for a metric series, days where the window
    wants finer resolution. `direction` is +1 when the event raises the measure and -1 when
    it lowers it. `min_len` is the shortest run that counts; `min_baseline` how many buckets
    must remain outside it (raise it for a single-signal test, where one noisy bucket must
    not become the whole baseline). Candidates are scored by magnitude * sqrt(len) so a
    genuine multi-bucket episode outranks one noisy bucket.

    Returns {start, end, inside, outside, n, score} with inclusive bucket keys, or None.
    O(B^2) over B buckets — 8 weeks or 56 days, so this is free."""
    keys = sorted(series)
    best = None
    for i in range(len(keys)):
        for j in range(i + min_len, len(keys) + 1):
            inside = [series[k] for k in keys[i:j]]
            outside = [series[k] for k in keys[:i]] + [series[k] for k in keys[j:]]
            if len(outside) < min_baseline:
                continue          # too little left over to be a baseline
            im = sum(inside) / len(inside)
            om = sum(outside) / len(outside)
            if direction * (im - om) <= 0:
                continue          # moves the wrong way for the signature being tested
            score = direction * (im - om) * (len(inside) ** 0.5)
            if best is None or score > best["score"]:
                best = {"start": keys[i], "end": keys[j - 1], "inside": im,
                        "outside": om, "n": len(inside), "score": score}
    return best


def split_in_out(rows, lo, hi, day_field="day"):
    """Rows inside an episode window, and the cohort's own baseline — everything outside
    it, on both sides."""
    inside = [r for r in rows if lo <= r[day_field] <= hi]
    outside = [r for r in rows if not (lo <= r[day_field] <= hi)]
    return inside, outside


def tenant_slug(tenant) -> str:
    """An id fragment for a tenant, derived from its WHOLE key rather than its first
    hyphen-segment. Two tenants sharing a prefix ('acme-bank', 'acme-cards') would
    otherwise collide into one finding id, and report.assemble asserts ids are unique —
    turning a cosmetic name clash into no report at all on a corpus we never get to see."""
    return "".join(ch if ch.isalnum() else "_" for ch in str(tenant)).strip("_")
