# SETUP — running the engine (for the product / screen team)

This is the runbook for the **engineering half**. The engine reads a deployment's logs and emits
one file — **`loop-report.json`** — which is the *only* interface between the engine and your
screen. You do not need to read or change any engine code; you run one command and build your
screen against the JSON it produces.

If you only remember one thing: **run the engine → get `loop-report.json` → render it → write the
human's APPROVE/REJECT back into it.** That's the whole contract.

---

## 1. Prerequisites

- **Python 3** (developed on 3.13; anything 3.9+ is fine). **No pip installs, no dependencies** —
  the engine is stdlib-only, on purpose, so it runs untouched on the sealed day-6 corpus.
- The practice **kit** must be present at `nexus-loop-day1/kit/` (corpus + catalog + labels). It
  ships with the repo but is git-ignored, so if you did a fresh clone that dropped it, get the kit
  from whoever has it before running.
- Run every command **from the repo root** (`Yellow.AI-1/`). Paths below assume that.

Quick sanity check:

```bash
python3 --version                 # 3.9+
ls nexus-loop-day1/kit            # should list corpus/, catalog.json, labels...
```

---

## 2. Quick start — produce `loop-report.json`

One command. Network-free, deterministic, ~seconds on the full corpus:

```bash
python3 -m engine.cli --kit nexus-loop-day1/kit --team solo --out engine/out/loop-report.json
```

You'll see:

```
wrote engine/out/loop-report.json  (12 metrics, 6 findings, 6 diagnoses, 3 gaps, 2 standard)
```

That file is your input. `engine/out/` is git-ignored, so treat it as a build artifact — re-run
the command any time to regenerate it.

**Preflight (automatic).** Before it reads the corpus, the engine validates the kit and prints to
**stderr**. On a clean kit you'll see nothing. If a column or file is off, you'll see either:

- `preflight WARNING: …` — the engine degrades around it and still writes a valid report (e.g. a
  missing `config_timeline` → findings still emit but attributions are null). Each warning is also
  recorded in the report's `system_notes` so it's visible downstream.
- `preflight FATAL — cannot produce a valid report:` followed by the exact missing file/column, and a
  non-zero exit. Nothing is written. Fix the kit and re-run.

Flags you may want:

| Flag | Effect |
|------|--------|
| `--out <path>` | where to write the report (default `engine/out/loop-report.json`) |
| `--team <name>` | stamps the `team` field in the report |
| `--sample` | runs on the 5% sample corpus (faster; **for spot-checks only** — always demo on the full corpus) |

---

## 3. What's inside `loop-report.json` (what your screen renders)

Top-level sections (the engine asserts internal link integrity on assembly — unique finding ids,
every diagnosis/prescription linked; consumed cleanly by the organisers' `score.py`):

| Section | What it is | Your screen uses it for |
|---------|-----------|-------------------------|
| `metrics` | canonical numbers, each with `fidelity` + `coverage` (+ `calibration` if judged) | "every number carries its definition" |
| `findings` | what broke — each real one carries `impact` (with a `derivation`), `audience`, `if_nothing_changes`, `evidence`, `window` | the main card a stranger reads |
| `diagnoses` | the cause (`cause_class`) + `attributed_change` | the "why" line |
| `prescriptions` | the proposed fix + a `decision` block (see below) | the APPROVE/REJECT card |
| `gaps` | what could **not** be measured, with a spec | the "honest refusal" panel |
| `standard` | the mined "good" the deployment is judged against | context / benchmark line |
| `verifications` | replay before/after (empty until you run §4) | the "we proved the fix" panel |
| `self_assessment` | the return arrow (opens after §4) | "how accurate were our predictions" |

Every finding with `is_regression: true` is guaranteed to have `impact`, `audience`, and
`if_nothing_changes` — so a card never shows a number without a decision attached.

The **prescription `decision`** block (always present) is exactly what the operator approves:

```json
"decision": {
  "asking_approval_for":     "…in the operator's words…",
  "risk_if_diagnosis_wrong": "…the honest downside…",
  "would_not_ship_if":       "…the condition that should stop this change…"
}
```

---

## 4. The APPROVE / REJECT write-back (your half of the loop)

The engine deliberately emits prescriptions **without** an `approval` — that is a *human* decision
your screen captures. It must **not** be fabricated by code. When the operator hits APPROVE/REJECT,
write an `approval` object back into that prescription in `loop-report.json`:

```json
"approval": {
  "verdict":    "accepted",          // "accepted" | "rejected" | "deferred"
  "decided_by": "operator name",
  "reason":     "why they decided this",
  "at":         "2026-09-15T14:03:00Z"
}
```

A **rejection is a valid, valuable answer** — the point is a real gate, not a rubber stamp. This
write-back is what turns the pipeline into a closed loop for scoring and for the demo.

---

## 5. (Optional) Verify a fix on the replay endpoint

This populates `verifications` + `self_assessment` with real before/after evidence. It needs the
organisers' replay endpoint running, and it is **opt-in** — the sealed run scores full marks
without it, so only do this for the demo's "we proved it works" beat.

**Terminal A — start the endpoint** (deterministic, non-LLM, 40-run budget):

```bash
python3 nexus-loop-day1/tools/nexus-loop-kit/replay/serve.py --kit nexus-loop-day1/kit --port 8719
# check it's up:  curl -s http://127.0.0.1:8719/health   ->  {"ok": true, ...}
```

**Terminal B — run verification against the report you built in §2:**

```bash
python3 -m engine.replay.client \
  --report engine/out/loop-report.json \
  --endpoint http://127.0.0.1:8719 \
  --team solo --kit nexus-loop-day1/kit
```

It POSTs each real prescription, records the `rp_…` outcome + prediction error back into the
report, and recomputes the return arrow. Add `--dry-run` to print the request bodies without
calling anything. **It is degradable:** if the endpoint is down it skips cleanly, keeps the
predicted deltas, and never crashes the report.

---

## 6. (Optional) Confirm the score yourself

Neither of these is required to run the engine — they're for confidence.

```bash
# organisers' scorer (needs the practice answer key; not available on the sealed corpus)
python3 nexus-loop-day1/tools/nexus-loop-kit/score.py \
  --report engine/out/loop-report.json \
  --ground-truth nexus-loop-day1/kit/ground_truth/ground_truth.json

# our self-check: score + the two-rule guards
python3 engine/selfcheck.py --report engine/out/loop-report.json
```

Both currently report **55.0 / 55** on the full variant-A corpus.

---

## 7. Two rules the product side must not break

These are the challenge's disqualifying non-negotiables. Nothing on the screen should violate them:

1. **No model/LLM for a fact the logs already record.** Every number in the report is computed
   from the corpus. Don't add a step that asks an LLM to re-derive or "improve" a number for
   display. (Models are only ever for *meaning*, e.g. clustering free text — and this engine uses
   none.)
2. **Never change a metric definition, the judge, or the "good" set to make a number look better.**
   Render the numbers as they are. If something reads badly, that's a finding, not a bug to tune
   away. The one thing the screen *does* write back is the human `approval` — that's a decision,
   not a metric.

---

## 8. Sealed-run reminder (day 6)

At the freeze the system must run **untouched, one command**, on a corpus nobody has seen:

```bash
python3 -m engine.cli --kit <sealed-kit-path> --team solo --out loop-report.json
```

No edits to the produced report are allowed. The replay stage (§5) is separate and opt-in. Point
your screen at whatever `--out` path you used and you're done.

If the sealed kit has a schema quirk, the preflight (above) tells you immediately: a **FATAL** line
names the exact missing file/column so it can be fixed before the clock runs out, and any **WARNING**
means the engine degraded around it and still produced a valid report (check `system_notes`). The
engine keys on **no** absolute day, tenant, or fault taxonomy, and is tested to hold as a cohort thins
to roughly a third of its practice volume — so the same command runs untouched on the sealed corpus.

---

## File map (for reference)

| Path | What |
|------|------|
| `engine/` | the engine (stdlib Python; you don't need to edit it) |
| `engine/cli.py` | the one entrypoint (§2) |
| `engine/preflight.py` | read-only kit validation run automatically before §2 |
| `engine/replay/client.py` | the opt-in verification stage (§5) |
| `engine/out/loop-report.json` | the artifact your screen reads (git-ignored) |
| `nexus-loop-day1/kit/` | corpus, catalog, labels |
| `nexus-loop-day1/tools/nexus-loop-kit/schema/loop-report.schema.json` | the report schema (field-by-field truth) |
| `nexus-loop-day1/tools/nexus-loop-kit/replay/serve.py` | the replay endpoint (§5) |
| `nexus-loop-day1/tools/nexus-loop-kit/score.py` | the organisers' scorer (§6) |
| `REPORT.md` | architecture + what's real/stubbed + challenges (source for the write-up & demo) |
