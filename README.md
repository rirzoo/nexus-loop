# Nexus Loop

Built for Yellow.ai TechQuest (IIT Patna) "Building an Autonomous Improvement
Loop for AI Agent Deployments." This particular build isn't the version I ended up
submitting; it's kept here as the fuller engineering pass at the problem.

Give it eight weeks of logs from an AI agent deployment it has never seen, and Nexus Loop hands an
operator a screen with three things: what broke, why it broke, and a fix to approve or reject.
Nothing shows up without evidence attached, and nothing ships without a person clicking a button.
That's the product: a decision someone can act on Monday morning, not a dashboard they have to
interpret.

Underneath that screen, the system mines each deployment's own definition of "good" from its own
traffic, finds the regressions hiding against that standard, works out the cause, proposes a fix,
and verifies the fix for real against a replay endpoint before it ever reaches a human for
approval. None of the detection logic knows in advance which day, tenant, or fault type to expect,
so it compares a deployment against itself rather than against a hardcoded pattern. That's what
let it hold a perfect 55.0/55 machine score across 80 independently regenerated corpora shaped
like the sealed exam, up from a mean of 35.4 on the first version that actually worked.

A few things worth reading closely if you're skimming:

- **The screen is the product.** A guided walk through one finding at a time, evidence and plain
  language before any number, and an APPROVE/REJECT that writes back into the report bound to its
  exact hash, so a decision can never end up attached to evidence nobody actually saw.
- **Every number carries its own honesty.** Fidelity, coverage, and, where it's judged, a
  calibration score. Anything the logs can't support comes back as a structured refusal instead of
  a guess, and the three decoys planted in the practice corpus get dismissed by reasoning about
  them, not by pattern-matching where they were planted.
- **The loop actually closes.** Detect, diagnose, prescribe, verify, self-assess: a proposed fix
  gets replayed against a live endpoint and the predicted delta gets checked against what actually
  happened, so the system keeps a track record instead of just a list of findings.
- **Two rules it can't break by construction**, not just by convention, checked mechanically by
  `engine/selfcheck.py`: never ask a model for a fact the logs already record, and never touch a
  metric definition or the judge to make a number look better.
- **No dependencies.** `engine/` and `screen/` are plain Python standard library, so the same code
  that runs on the practice corpus runs untouched on a sealed one it's never seen.

The full write-up, with figures from the running screen, is [`Report.pdf`](Report.pdf).

## See it work in one command

```bash
python3 run.py
```

This builds `loop-report.json` if it doesn't exist yet, serves the operator screen at
`http://127.0.0.1:8080`, opens it in a browser, and then prints every decision you record on
screen: what changed in the file, and what it did to the scorer's checklist. That way the
write-back is visible instead of something you have to take on trust.

Requires only Python 3.9+ and the standard library. No installs, no network calls except the
optional replay-verification stage.

## Repo layout

| Path | What it is |
|---|---|
| `engine/` | The analysis core: ingestion, metrics, standard-mining, detection, diagnosis, prescription, replay verification, report assembly. Stdlib Python only. |
| `screen/` | Loop Desk, the operator screen. One HTML file (`index.html`) plus a small write-back server (`serve.py`). No build step, no dependencies. |
| `run.py` | Starts the engine and the screen together and narrates every decision you record. |
| `nexus-loop-day1/` | The organisers' kit: corpus, catalog, scoring script (`tools/nexus-loop-kit/score.py`), ground truth, schema, and the replay endpoint. |
| `Report.pdf` | The compiled write-up. |
| `SETUP.md` | The full runbook, for when `python3 run.py` isn't enough. |

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

Optional: verify a proposed fix for real against the replay endpoint. It needs two terminals; see
**§5** of `SETUP.md`.

## The two rules the system cannot break

1. **Never ask a language model for a fact the logs already record.** Models are only used for
   questions of meaning (was the user satisfied, what was this conversation about), never for
   facts the corpus already logs.
2. **Never change a metric definition, judge version, or the reviewed "good" set to make a number
   look better.** The system may propose changes to the agent, never to the yardstick.

Both are enforced mechanically, not just by convention. See `engine/selfcheck.py` and the
Assumptions and Transparency section of the write-up.

## Where to go next

- New to the project? Start with `SETUP.md`.
- Want the full technical write-up? `Report.pdf`.
- Want to know what happens on a corpus with a fault the engine can't classify?
  `python3 run.py --unknown`.
