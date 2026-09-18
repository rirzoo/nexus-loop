# SETUP: running the engine

This is the runbook for the engine side of the project. The engine reads a deployment's logs and
emits one file, `loop-report.json`, which is the only interface between the engine and the screen.
You don't need to read or change any engine code; you run one command and build a screen against
the JSON it produces.

If you only remember one thing: run the engine → get `loop-report.json` → render it → write the
human's APPROVE/REJECT back into it. That's the whole contract.

---

## 0. Just want to see it work?

```bash
python3 run.py
```

Builds the report if it is missing, serves the screen at `http://127.0.0.1:8080`, opens it,
and then watches the report file. Every approval, rejection, or deferral you record on screen
is printed in the terminal with what changed and what it did to the scorer's decision count,
so you can see the write-back rather than take it on trust.

| Flag | Effect |
|------|--------|
| `--rebuild` | regenerate the report first. Asks first, because it clears recorded decisions |
| `--unknown` | build and serve a corpus containing a fault the engine cannot classify |
| `--sample` | build from the 5% sample corpus |
| `--port N` | serve somewhere other than 8080 |
| `--no-open` | do not open a browser |

It does not rebuild by default, so decisions survive a restart. They do not survive a rebuild,
and that is deliberate: an approval is bound to the report's content hash, so it is a decision
about the exact evidence someone read. Regenerate the report and the hash changes, which is why
the screen refuses a decision made against evidence that has moved. Try it: leave the page open,
run `python3 -m engine.cli …` in another terminal, then approve something. The write is refused
and the page tells you to reload.

The rest of this document is the manual version of the same thing.

---

## 1. Prerequisites

- Python 3. Developed on 3.13, but anything 3.9 or later works. No pip installs and no
  dependencies: the engine is stdlib only, on purpose, so it runs untouched on the sealed day-6
  corpus.
- The practice kit ships with the repo at `nexus-loop-day1/kit/` (corpus, catalog, labels), so a
  fresh clone already has everything it needs.
- Run every command from the repo root (`Yellow.AI-1/`). Paths below assume that.

Quick sanity check:

```bash
python3 --version                 # 3.9+
ls nexus-loop-day1/kit            # should list corpus/, catalog.json, labels...
```

---

## 2. Quick start: produce `loop-report.json`

One command. Network-free, deterministic, ~seconds on the full corpus:

```bash
python3 -m engine.cli --kit nexus-loop-day1/kit --team solo --out engine/out/loop-report.json
```

You'll see:

```
wrote engine/out/loop-report.json  (12 metrics, 6 findings, 6 diagnoses, 3 gaps, 2 standard)
```

That file is your input. `engine/out/` is git-ignored, so treat it as a build artifact. Re-run
the command any time to regenerate it.

Preflight runs automatically: before it reads the corpus, the engine validates the kit and prints
to `stderr`. On a clean kit you'll see nothing. If a column or file is off, you'll see one of two
things:

- `preflight WARNING: …`: the engine degrades around it and still writes a valid report. For
  example, a missing `config_timeline` means findings still emit, but attributions come back
  null. Each warning is also recorded in the report's `system_notes`, so it's visible downstream.
- `preflight FATAL — cannot produce a valid report:` followed by the exact missing file or column,
  and a non-zero exit. Nothing is written. Fix the kit and re-run.

Flags you may want:

| Flag | Effect |
|------|--------|
| `--out <path>` | where to write the report (default `engine/out/loop-report.json`) |
| `--team <name>` | stamps the `team` field in the report |
| `--sample` | runs on the 5% sample corpus (faster; **for spot-checks only** — always demo on the full corpus) |

---

## 3. What's inside `loop-report.json` (what a screen renders)

Top-level sections. The engine asserts internal link integrity on assembly, unique finding ids,
every diagnosis and prescription linked, and the result is consumed cleanly by the organisers'
`score.py`:

| Section | What it is | A screen uses it for |
|---------|-----------|-------------------------|
| `metrics` | canonical numbers, each with `fidelity` + `coverage` (+ `calibration` if judged) | "every number carries its definition" |
| `findings` | what broke — each real one carries `impact` (with a `derivation`), `audience`, `if_nothing_changes`, `evidence`, `window` | the main card a stranger reads |
| `diagnoses` | the cause (`cause_class`) + `attributed_change` | the "why" line |
| `prescriptions` | the proposed fix + a `decision` block (see below) | the APPROVE/REJECT card |
| `gaps` | what could **not** be measured, with a spec | the "honest refusal" panel |
| `standard` | the mined "good" the deployment is judged against | context / benchmark line |
| `verifications` | replay before/after (empty until you run §5) | the "we proved the fix" panel |
| `self_assessment` | the return arrow (opens after §5) | "how accurate were our predictions" |
| `summary_plain` | the whole run in plain words, deliberately with no figures in it | the opening paragraph |
| `whats_real` | `{key, claim, status, why}` per claim — what is real, what is templated, what has not been run | the honesty panel |

Every finding with `is_regression: true` is guaranteed to have `impact`, `audience`, and
`if_nothing_changes`, so a card never shows a number without a decision attached.

### The plain-language fields

`engine/narrate.py` adds a wording layer beside the numbers, so a screen does not have to
invent sentences from raw fields. They are additive: the schema allows them and `score.py`
ignores them. They are also inside `report_build`, so a recorded decision is bound to the
words the operator read.

On every finding:

| Field | What it is |
|-------|-----------|
| `title_plain` | the problem in one sentence, with no numbers in it |
| `where_plain` | the slice in an operator's words, e.g. "the get_order_status tool, on northwind-retail" |
| `what_happened` | two to four sentences explaining it without jargon |
| `cause_plain` | the cause and the change behind it, or a plain statement that none lines up |
| `status_short` / `status_plain` | how long it ran and whether it is traced to anything |
| `baseline` + `baseline_plain` | `{axis: "peer" \| "own_past", value, source, why}` — which yardstick and why that one |
| `rank` | 1..n over the regressions, by severity then size. Order your list by this |
| `needs_your_inference` | `true` when the cause class is `unknown`. Skip the cause and the fix; ask the operator instead |
| `effect_plain` | ordered `[{key, headline, detail, field}]`, the PS's impact list |
| `outcome_band` | `{total, segments: [{key, label, count}], caption}` — what became of those conversations |
| `dismissed_plain` | on `is_regression: false` only: why it is not a fault |

Two fields need extra care, since they differ per finding. `effect_plain` only carries the blocks
whose data exists: `impact.downstream` differs by detector, so loop the list and render what's in
it rather than indexing by position or assuming a fixed set (`field` is the dotted path, if you
want to attach your own click-through). `outcome_band` is absent when the fault never moved an
outcome: a cost-and-turns regression resolves at the same rate it always did, so there's no split
to draw, and drawing one would imply harm that isn't there. Fall back to observed against baseline
in that case.

Also present: `predicted_plain` on each prescription, and `plain: {asked, refused_because,
what_would_have_to_exist}` on each gap.

One `cause_class` needs its own handling: `unknown`, a confirmed regression the engine couldn't
classify, whose prescription proposes no fix (`change_type: "no_action"`). It never fires on the
practice corpus but can on the sealed one. It renders as a normal finding card, but since there's
no fix to approve, the operator is being asked to investigate rather than to ship. If your UI maps
cause class to a label, icon, or fix description, give this value its own branch or a sane
default. Run `python3 run.py --unknown` to build and serve a fixture report containing one.

The **prescription `decision`** block (always present) is exactly what the operator approves:

```json
"decision": {
  "asking_approval_for":     "…in the operator's words…",
  "risk_if_diagnosis_wrong": "…the honest downside…",
  "would_not_ship_if":       "…the condition that should stop this change…"
}
```

---

## 4. The APPROVE / REJECT write-back (the other half of the loop)

The engine deliberately emits prescriptions without an `approval`. That's a human decision the
screen captures, and it must not be fabricated by code. When the operator hits APPROVE/REJECT,
write an `approval` object back into that prescription in `loop-report.json`:

```json
"approval": {
  "verdict":    "accepted",          // "accepted" | "rejected" | "deferred"
  "decided_by": "operator name",
  "confidence": "high",              // "low" | "medium" | "high"
  "reason":     "why they decided this",
  "at":         "2026-09-15T14:03:00Z"
}
```

`confidence` is the operator's own stated confidence in the call, kept separate from the
detector's `cause_class` confidence on the diagnosis. "How sure was the machine" and "how sure
was the person who signed off" are two different numbers, and the screen never conflates them.

A rejection is a valid, valuable answer. The point is a real gate, not a rubber stamp, and this
write-back is what turns the pipeline into a closed loop, both for scoring and for the demo.

---

## 5. (Optional) Verify a fix on the replay endpoint

This populates `verifications` and `self_assessment` with real before/after evidence. It needs
the organisers' replay endpoint running, and it is opt-in. The sealed run scores full marks
without it, so only do this for the demo's "we proved it works" beat.

**Terminal A, start the endpoint** (deterministic, non-LLM, 40-run budget):

```bash
python3 nexus-loop-day1/tools/nexus-loop-kit/replay/serve.py --kit nexus-loop-day1/kit --port 8719
# check it's up:  curl -s http://127.0.0.1:8719/health   ->  {"ok": true, ...}
```

**Terminal B, run verification against the report you built in §2:**

```bash
python3 -m engine.replay.client \
  --report engine/out/loop-report.json \
  --endpoint http://127.0.0.1:8719 \
  --team solo --kit nexus-loop-day1/kit
```

It posts each real prescription, records the `rp_…` outcome and prediction error back into the
report, and recomputes the return arrow. Add `--dry-run` to print the request bodies without
calling anything. It's degradable: if the endpoint is down, it skips cleanly, keeps the predicted
deltas, and never crashes the report.

---

## 6. (Optional) Confirm the score yourself

Neither of these is required to run the engine. They're here for confidence.

```bash
# organisers' scorer (needs the practice answer key; not available on the sealed corpus)
python3 nexus-loop-day1/tools/nexus-loop-kit/score.py \
  --report engine/out/loop-report.json \
  --ground-truth nexus-loop-day1/kit/ground_truth/ground_truth.json

# our self-check: score + the two-rule guards
python3 engine/selfcheck.py --report engine/out/loop-report.json
```

Both currently report **55.0 / 55** on the full variant-A corpus.

The variant-A score is necessary but not sufficient. The practice corpus plants its faults at the
very end of the eight weeks, and the sealed one does not, so the number that actually predicts
day 6 comes from here:

```bash
python3 engine/stress_sealed.py --n 80
```

This builds corpora the way the organisers build the sealed one (from a passphrase, generated in
memory, with no files written), runs the engine untouched on each, and scores them with the
organisers' `score.py`. It currently reports **mean 55.00 / 55, min 55.00** over 80 layouts. Run
it with a fresh `--prefix` before the freeze; it exits non-zero if any layout scores below 55.

---

## 7. Two rules the product side must not break

These are the challenge's disqualifying non-negotiables. Nothing on the screen should violate
them:

1. No model or LLM for a fact the logs already record. Every number in the report is computed
   from the corpus, so don't add a step that asks an LLM to re-derive or "improve" a number for
   display. (Models are only ever used for meaning, such as clustering free text, and this engine
   uses none.)
2. Never change a metric definition, the judge, or the "good" set to make a number look better.
   Render the numbers as they are: if something reads badly, that's a finding, not a bug to tune
   away. The one thing the screen does write back is the human `approval`, and that's a decision,
   not a metric.

---

## 8. Sealed-run reminder (day 6)

At the freeze, the system has to run untouched, in one command, on a corpus nobody has seen:

```bash
python3 -m engine.cli --kit <sealed-kit-path> --team solo --out loop-report.json
```

No edits to the produced report are allowed. The replay stage (§5) is separate and opt-in. Point
your screen at whatever `--out` path you used and you're done.

If the sealed kit has a schema quirk, the preflight (above) tells you immediately: a `FATAL` line
names the exact missing file or column so it can be fixed before the clock runs out, and any
`WARNING` means the engine degraded around it and still produced a valid report (check
`system_notes`).

The engine keys on no absolute day, tenant, fault taxonomy, or position of a fault within the
eight weeks. The evidence for that last claim is the sweep in §6: 80 corpora generated from the
same `sealed_schedule` function the organisers use, all scoring 55.0 / 55 with the engine
untouched. It matters because the practice corpus plants its faults in the last fortnight, the
sealed one usually plants them in the first, and detectors that only recognise a permanent step
score around 35 on the latter. The engine also holds up as a cohort thins to roughly a third of
its practice volume, so the same command runs untouched on the sealed corpus.

---

## 9. The screen: Loop Desk

A working screen already ships at `screen/index.html`, served by `screen/serve.py`. It reads
whatever `--report` you point it at and is the write-back target for §4, so you don't need to
build a screen from scratch to demo the loop end to end.

```bash
python3 screen/serve.py --report engine/out/loop-report.json
# open http://127.0.0.1:8080
```

`--report`, `--page`, `--port`, and `--host` are all overridable (defaults above). The server
serves the one HTML file (no dependencies, no build step) and exposes `GET /report` and
`POST /decision`, which is the only thing that ever writes into the report. `screen/index.html`'s
data layer and this write path are untouched by the redesign below.

The layout has two panes. The left is the ledger: a plain-language summary of the whole run with
no figures in it, then "Needs you" (one row per regression, ordered by severity, each carrying a
small band of what became of those conversations, so you can rank the faults by shape before
reading a single number), then a quieter "For context" list: the lookalikes we set aside, the
questions we refused, our own hit rate, and what this system actually does.

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

Every proportion is drawn rather than spelled out: shares, coverage, confidence, and
observed-against-baseline are all bars. Clicking any bar or figure opens its definition: what it
is, how we know it (measured, derived, or judged), what share of traffic can produce it, what's
excluded, its calibration if it's judged, and how it was computed.

Navigation: click a row to open it, then `←` / `→` or the Back/Next buttons. The current slide is
in the URL (`#/f/<finding_id>/3`, `#/gaps/0`), so you can deep-link straight to a slide during
questions and browser back/forward works.

Verified in a real browser (Chrome, 1536×751): all six slides on all three findings, the
dismissed-lookalike and refused-question sequences, the honesty inventory, the `unknown`-cause
fixture (`engine/out/loop-report-unknown.json`, built by `python3 run.py --unknown`), and an
end-to-end `POST /decision` write-back that lands in `prescriptions[].approval` bound to the
build hash. Not verified: the phone layout. The media query below 900px is written, but the
browser tool in this environment wouldn't resize the window, so nobody has actually looked at it.
Open it narrow once before relying on it.

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
| `engine/out/loop-report.json` | the artifact the screen reads (git-ignored) |
| `screen/index.html` | the Loop Desk screen (§9) — guided six-slide console, no deps |
| `screen/serve.py` | serves the screen and handles the `POST /decision` write-back (§9) |
| `nexus-loop-day1/kit/` | corpus, catalog, labels |
| `nexus-loop-day1/tools/nexus-loop-kit/schema/loop-report.schema.json` | the report schema (field-by-field truth) |
| `nexus-loop-day1/tools/nexus-loop-kit/replay/serve.py` | the replay endpoint (§5) |
| `nexus-loop-day1/tools/nexus-loop-kit/score.py` | the organisers' scorer (§6) |
