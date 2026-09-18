# Nexus Loop — day-0 kit generator

Builds the corpus, catalog, labels, replay endpoint and scoring harness for the
[Nexus Loop hackathon](../../docs/hackathon/nexus-loop-hackathon.md).

**Stdlib Python 3.9+. No dependencies.** Nothing to install, nothing to pin, and it generates
**byte-identically** from a given seed — which matters when thirty people need the same corpus at
09:00 on day 1, and again when you re-run a sealed corpus after the event.

```bash
cd tools/nexus-loop-kit

make kit                              # the practice corpus teams get on day 0
make sealed SECRET="a phrase you keep"   # the day-6 corpus
make serve                            # the replay endpoint
make check                            # score the level-3 reference report
```

Or directly:

```bash
python3 generate.py --variant A --out ../../kit
python3 generate.py --sealed "a phrase you keep" --out ../../kit-sealed
python3 starter.py  --kit ../../kit --team demo --out /tmp/r.json
python3 score.py    --report /tmp/r.json --ground-truth ../../kit/ground_truth/ground_truth.json
python3 replay/serve.py --kit ../../kit --port 8719
```

---

## Keeping the day-6 test honest

Three things stop a team with this repo from short-circuiting the sealed run. **If you change
anything in here, keep all three.**

**The sealed layout is derived from a passphrase, not stored.** `FAULT_SCHEDULE` holds only the
practice corpus. The sealed one comes from `sealed_schedule(secret)` — a deterministic layout keyed
on a string only you have. Nobody can regenerate it from this repo alone, and the same secret always
rebuilds the same corpus, so a disputed result can be reproduced afterwards.

**`replay/serve.py` contains no answers.** It loads its effect model from the ground truth of
whichever corpus it is serving. Point it at the sealed ground truth on day 6 and run it centrally;
teams get a URL, not the file.

**"Nothing happened" is one answer, not two.** A wrong fix and a cohort with nothing wrong in it
return the same `no_effect`. If those were distinguishable, forty probes would map every fault
without any analysis. There is also a 40-run budget per team, logged at `GET /log`.

`ground_truth/` **does** ship inside the practice kit — teams need it to score themselves all week.
`ground_truth_SEALED/` is written next to the sealed corpus and is the one thing you never
distribute.

---

## What comes out

Per corpus, ~18 s and ~34 MB:

| | |
|---|---|
| `corpus/sessions.jsonl.gz` | ~80,000 conversations across 2 tenants × 8 weeks |
| `corpus/agent_steps.jsonl.gz` | ~880,000 execution steps — turn, llm_call, tool_call, kb_lookup, guardrail, handoff |
| `corpus/turns.jsonl.gz` | ~364,000 transcript turns |
| `corpus/config_timeline.csv` | 16 config changes — model, prompt, KB, tool, routing, judge |
| `corpus/feedback.csv` | sparse CSAT (~8.6% of sessions, not missing at random) |
| `corpus_sample/` | a 5% **whole-session** slice for fast iteration — same schema, same joins |
| `catalog.json` | the semantic catalog: entities, grains, field semantics, join validity, capability + coverage declarations, cardinality budgets |
| `labels/` | 400 human outcome labels + ~100 rubric scores, with ~7.5% human disagreement baked in |
| `asks.md` | the 11 operator asks |
| `manifest.json` | row counts and file descriptions |
| `ground_truth/` | **organiser + practice only** — what was injected, and the measured effect of each |

## What's in the data

Three regressions, three decoys, two coverage traps, one unanswerable ask. The sealed corpus moves
all of them.

**The regressions** are shaped so each defeats a different naive approach:

- **A knowledge gap** on a product line that launches mid-corpus. There is no before-period for the
  cohort — it is born broken. Only a comparison against a *standard* catches it.
- **A silent tool failure**: HTTP 200, `outcome: "ok"`, empty payload. The declared tool-error metric
  stays flat across the whole window. It is only visible in `response_bytes`, `result_field_count`,
  and a same-tool retry in the next turn.
- **A prompt regression** that raises median turns and cost sharply while leaving resolution rate
  *flat*. Every outcome-threshold alert misses it.

**The decoys** each punish a different shortcut:

- **A traffic-mix shift** where aggregate containment *rises* because a trivially-contained intent
  balloons. Every cohort is flat.
- **A load event**: several times the volume, tool p95 doubling, real timeouts — and quality flat
  throughout. A reliability event, not a quality regression.
- **A judge-version boundary**: the rubric goes v1 → v2 mid-corpus and judged quality drops on *both
  tenants simultaneously*. `judge_version` is stamped on every scored session, so the information
  needed to refuse the comparison is right there.

**The coverage traps**: legacy flow traffic emits no `tool_call` rows at all, so any tool-derived
metric has a silently wrong denominator unless it says so. And `custom_dims.customer_ref` is
near-unique with a declared cardinality budget — the correct response to "group by it" is a refusal
citing the budget.

## Scoring

**55 points machine, 45 judged by people.** The harness scores diagnostic accuracy, specificity,
honesty and loop completeness, then prints a **judges' checklist** — how many findings carry an
impact block, how many show their working, how many prescriptions were actually decided on — so
several judges score the same work the same way.

Reference reports, all scored by `score.py`:

| | Machine |
|---|---|
| `starter.py` output | **9.8 / 55** |
| `examples/loop-report.level1.json` | **38.0 / 55** |
| `examples/loop-report.level2.json` | **41.5 / 55** |
| `examples/loop-report.level3.json` | **46.3 / 55** |

A pipeline that finds every fault and explains it to nobody cannot pass 55.

## Layout

```
generate.py              CLI — builds one corpus
starter.py               what teams run first: orientation + two honest metrics + a valid report
score.py                 the judging harness
replay/serve.py          the verification endpoint (loads its model from ground truth)
schema/                  loop-report.schema.json — the one deliverable
examples/                level1 / level2 / level3 reference reports
nlkit/
  world.py               tenants, intents, tools, closed value sets, the config timeline,
                         the practice schedule, and sealed_schedule()
  simulate.py            the session simulator
  catalog.py             the semantic catalog
  labels.py              human label sampling, with realistic disagreement
  asks.py                the eleven operator asks
```

To change what the hackathon tests, edit the practice schedule and `ground_truth()` in
`nlkit/world.py`; everything else follows from them. If you add a fault, add its
`accepted_fix_classes` — the replay endpoint reads them from the ground truth and needs nothing else.
