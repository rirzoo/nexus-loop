# SETUP — running the engine (for the product / screen team)

This is the runbook for the **engineering half**. The engine reads a deployment's logs and emits
one file — **`loop-report.json`** — which is the *only* interface between the engine and your
screen. You do not need to read or change any engine code; you run one command and build your
screen against the JSON it produces.

If you only remember one thing: **run the engine → get `loop-report.json` → render it → write the
human's APPROVE/REJECT back into it.** That's the whole contract.

---

## 0. Just want to see it work?

```bash
python3 run.py
```

Builds the report if it is missing, serves the screen at `http://127.0.0.1:8080`, opens it,
and then watches the report file. Every approval, rejection or deferral you record on screen
is printed in the terminal with what changed and what it did to the scorer's decision count,
so you can see the write-back rather than take it on trust.

| Flag | Effect |
|------|--------|
| `--rebuild` | regenerate the report first. Asks first, because it clears recorded decisions |
| `--unknown` | build and serve a corpus containing a fault the engine cannot classify |
| `--sample` | build from the 5% sample corpus |
| `--port N` | serve somewhere other than 8080 |
| `--no-open` | do not open a browser |

It does not rebuild by default, so decisions survive a restart. They do not survive a
rebuild, and that is deliberate: an approval is bound to the report's content hash, so it
is a decision about the exact evidence someone read. Regenerate the report and the hash
changes, which is why the screen refuses a decision made against evidence that has moved.
Try it: leave the page open, run `python3 -m engine.cli …` in another terminal, then approve
something. The write is refused and the page tells you to reload.

The rest of this document is the manual version of the same thing.

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

One `cause_class` needs its own handling: **`unknown`** — a confirmed regression the engine could not
classify, whose prescription proposes no fix (`change_type: "no_action"`). It never fires on the
practice corpus but can on the sealed one. See **[`UNKNOWN_CAUSE_CLASS.md`](UNKNOWN_CAUSE_CLASS.md)**
for what to render, the gotchas, and how to generate a fixture report containing one.

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

The variant-A score is necessary but not sufficient — the practice corpus plants its faults at the
very end of the eight weeks, and the sealed one does not. The number that actually predicts day 6:

```bash
python3 engine/stress_sealed.py --n 80
```

This builds corpora the way the organisers build the sealed one (from a passphrase, in memory — no
files written), runs the engine untouched on each, and scores them with the organisers' `score.py`.
It currently reports **mean 55.00 / 55, min 55.00** over 80 layouts. Run it with a fresh `--prefix`
before the freeze; it exits non-zero if any layout scores below 55.

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
means the engine degraded around it and still produced a valid report (check `system_notes`).

The engine keys on **no** absolute day, tenant, fault taxonomy, or position of a fault within the eight
weeks. The evidence for that last one is the sweep in §6: 80 corpora generated from the same
`sealed_schedule` function the organisers use, all scoring 55.0 / 55 with the engine untouched. (It
matters: the practice corpus plants its faults in the last fortnight, the sealed one usually plants
them in the first, and detectors that only recognise a permanent step score ~35 on the latter.) The
engine also holds as a cohort thins to roughly a third of its practice volume. So the same command runs
untouched on the sealed corpus.

---

## 9. The screen — Loop Desk

A working screen already ships at `screen/index.html`, served by `screen/serve.py`. It reads
whatever `--report` you point it at and is the write-back target for §4 — you do not need to
build a screen from scratch to demo the loop end to end.

```bash
python3 screen/serve.py --report engine/out/loop-report.json
# open http://127.0.0.1:8080
```

`--report`/`--page`/`--port`/`--host` are all overridable (defaults above). The server serves the
one HTML file (no deps, no build) and exposes `GET /report` + `POST /decision`, which is the only
thing that ever writes into the report — `screen/index.html`'s data layer and this write path are
untouched by the redesign below.

**Layout.** Two panes. The left is the ledger: a plain-language summary of the whole run with no
figures in it, then "Needs you" (one row per regression, ordered by severity, each carrying a
small band of what became of those conversations so you can rank the faults by shape before
reading a single number), then a quieter "For context" list — the lookalikes we set aside, the
questions we refused, our own hit rate, and what this system really does.

The right pane is a six-slide walk through one finding, taken at the reader's pace:

| Slide | What is on it |
|-------|---------------|
| 1 · The problem | One plain sentence, alone. Nothing else competes with it. |
| 2 · What it did | The outcome band, then how many conversations, how many should have been sorted and were not, how many reached a person who was never meant to be involved, how many gave up, how long it ran and what it cost — plus who has to act and what happens if nobody does. |
| 3 · Why it happened | The cause in plain words, the configuration change behind it, how sure we are, and what we ruled out. |
| 4 · The evidence | The baseline named and justified first, then observed against baseline, then coverage stated out loud, then the measurements and the full derivation. |
| 5 · The fix | What is being asked of you, the predicted move, the risk if the diagnosis is wrong, what would stop us shipping, and the replay proof — or a plain statement that there is none. |
| 6 · Decide | APPROVE / REJECT / DEFER, written back into the report. |

A fault whose cause could not be classified skips slides 3 and 5 and asks you for your own reading
instead; that goes into the report as the decision's reason.

**Every proportion is drawn rather than spelled** — shares, coverage, confidence and
observed-against-baseline are all bars. Clicking any bar or figure opens its definition: what it
is, how we know it (measured, derived or judged), what share of traffic can produce it, what is
excluded, its calibration if it is judged, and how it was computed.

Navigation: click a row to open it, then `←` / `→` or the Back/Next buttons. The current slide is
in the URL (`#/f/<finding_id>/3`, `#/gaps/0`), so you can deep-link straight to a slide during
questions and browser back/forward works.

**Verified in a real browser** (Chrome, 1536×751): all six slides on all three findings, the
dismissed-lookalike and refused-question sequences, the honesty inventory, the `unknown`-cause
fixture (`engine/out/loop-report-unknown.json` — see
[`UNKNOWN_CAUSE_CLASS.md`](UNKNOWN_CAUSE_CLASS.md)), and an end-to-end `POST /decision` write-back
that lands in `prescriptions[].approval` bound to the build hash. **Not verified:** the phone
layout — the media query below 900px is written but the browser tool in this environment would not
resize the window, so nobody has actually looked at it. Open it narrow once before relying on it.

---

## File map (for reference)

| Path | What |
|------|------|
| `run.py` | starts the engine + screen together and narrates your decisions (§0) |
| `engine/` | the engine (stdlib Python; you don't need to edit it) |
| `engine/cli.py` | the one entrypoint (§2) |
| `engine/preflight.py` | read-only kit validation run automatically before §2 |
| `engine/replay/client.py` | the opt-in verification stage (§5) |
| `engine/narrate.py` | the plain-language layer the screen reads (templates, no model) |
| `engine/stress_sealed.py` | the sealed-layout sweep (§6) — scores the engine on corpora shaped like day 6's |
| `engine/out/loop-report.json` | the artifact your screen reads (git-ignored) |
| `screen/index.html` | the Loop Desk screen (§9) — guided six-slide console, no deps |
| `screen/serve.py` | serves the screen and handles the `POST /decision` write-back (§9) |
| `nexus-loop-day1/kit/` | corpus, catalog, labels |
| `nexus-loop-day1/tools/nexus-loop-kit/schema/loop-report.schema.json` | the report schema (field-by-field truth) |
| `nexus-loop-day1/tools/nexus-loop-kit/replay/serve.py` | the replay endpoint (§5) |
| `nexus-loop-day1/tools/nexus-loop-kit/score.py` | the organisers' scorer (§6) |
| `UNKNOWN_CAUSE_CLASS.md` | the `unknown` cause class — what the screen must handle (§3) |
| `REPORT.md` | architecture + what's real/stubbed + challenges (source for the write-up & demo) |
