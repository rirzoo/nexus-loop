# Nexus Loop

**Team Shekhar · IIT Patna** — built for Yellow.ai TechQuest, "Building an Autonomous Improvement
Loop for AI Agent Deployments." This build is not the version the team ultimately submitted; it's
kept here as the fuller engineering exploration of the problem.

Nexus Loop reads eight weeks of an AI-agent deployment's logs it has never seen, mines the
deployment's *own* definition of "good" from its own traffic, finds the regressions hiding in it,
explains why they happened and who has to act, proposes a fix, verifies the fix for real against a
replay endpoint, and hands the final call to a human through an operator screen. Nothing in the
detection code knows in advance which day, tenant, or fault type it's looking for — it finds
regressions by comparing a deployment against itself, which is what let it hold a perfect
55.0 / 55 machine score across 80 independently regenerated sealed-shaped corpora, up from a 35.4
mean on the first version that worked at all.

A few things worth reading closely if you're skimming:

- **It refuses to bluff.** Every reported number carries fidelity and coverage; anything the logs
  can't support comes back as a structured refusal (`NOT_MEASURABLE`, `REQUIRES_NEW_JUDGE`, ...)
  instead of a guess. The three planted decoys in the practice corpus are dismissed by principle
  (not sustained, not localized, not a real outcome shift) rather than by pattern-matching them.
- **It closes the loop, not just detects.** Detect → Diagnose → Prescribe → Verify → Self-assess:
  a fix is proposed, actually replayed against a live endpoint, and the predicted-vs-observed
  delta is scored — the loop reports on its own track record instead of just its findings.
- **A human still approves every fix.** The operator screen (Loop Desk) binds each APPROVE/REJECT
  decision to the exact report hash it was shown, so the write-back is auditable, not implicit.
- **Two rules it can't break by construction** (not just convention — enforced by
  `engine/selfcheck.py`): never ask a model for a fact the logs already record, and never touch
  the metric definitions or the judge to make a number look better.
- **Zero dependencies.** `engine/` and `screen/` are stdlib Python only, so the same code runs
  untouched on a sealed corpus it's never seen.

The full write-up, with figures from the running screen, is [`Final Report/Report.pdf`](Final%20Report/Report.pdf).

## See it work in one command

```bash
python3 run.py
```

This builds `loop-report.json` if it doesn't exist yet, serves the operator screen at
`http://127.0.0.1:8080`, opens it in a browser, and then prints every decision you record on
screen — what changed in the file and what it did to the scorer's checklist — so the write-back
is visible instead of something you have to take on trust.

Requires only Python 3.9+ and the standard library. No installs, no network calls except the
optional replay-verification stage.

## Repo layout

| Path | What it is |
|---|---|
| `engine/` | The analysis core: ingestion, metrics, standard-mining, detection, diagnosis, prescription, replay verification, report assembly. Stdlib Python only. |
| `screen/` | Loop Desk — the operator screen. One HTML file (`index.html`) plus a small write-back server (`serve.py`). No build step, no dependencies. |
| `run.py` | Starts the engine and the screen together and narrates every decision you record. |
| `nexus-loop-day1/` | The organisers' kit: corpus, catalog, scoring script (`tools/nexus-loop-kit/score.py`), ground truth, schema, and the replay endpoint. |
| `Final Report/` | The compiled write-up (`Report.pdf`) and its LaTeX source. |
| `SETUP.md` | The full runbook — read this if `python3 run.py` isn't enough. |

## Running the pieces individually

```bash
# Build the report from the kit
python3 -m engine.cli --kit nexus-loop-day1/kit --team solo --out engine/out/loop-report.json

# Self-score it against the organisers' scorer
python3 nexus-loop-day1/tools/nexus-loop-kit/score.py \
  --report engine/out/loop-report.json \
  --ground-truth nexus-loop-day1/kit/ground_truth/ground_truth.json

# Non-negotiable checks (fidelity/coverage/calibration honesty, etc.)
python3 engine/selfcheck.py --report engine/out/loop-report.json

# Serve just the screen against an existing report
python3 screen/serve.py --report engine/out/loop-report.json

# Unit tests + the sealed-layout robustness sweep
python3 -m unittest discover -s engine -p "test_*.py"
python3 engine/stress_sealed.py --n 20
```

Optional: verify a proposed fix for real against the replay endpoint (needs two terminals) —
see **§5** of `SETUP.md`.

## The two rules the system cannot break

1. **Never ask a language model for a fact the logs already record.** Models are only used for
   questions of meaning (was the user satisfied, what was this conversation about), never for
   facts the corpus already logs.
2. **Never change a metric definition, judge version, or the reviewed "good" set to make a number
   look better.** The system may propose changes to the agent, never to the yardstick.

Both are enforced mechanically, not just by convention — see `engine/selfcheck.py` and the
Assumptions & Transparency section of the report.

## Where to go next

- New to the project? Start with `SETUP.md`.
- Want the full technical write-up? `Final Report/Report.pdf`.
- Want to know what happens on a corpus with a fault the engine can't classify?
  `python3 run.py --unknown`.
