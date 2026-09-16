# The `unknown` cause class — what the screen needs to handle

**Short version:** `cause_class` can now be `"unknown"`. It means *"we confirmed a real regression
but could not name its cause."* It renders as a normal finding card, but its prescription has no fix
to approve — the human is being asked to **investigate**, not to ship. If your UI maps cause class to
a label, icon, or fix description, it needs a branch (or a sane default) for this value.

This never appeared on the practice corpus, which is why you may not have seen it. It can appear on
the sealed corpus. Nothing else about the report shape changed.

---

## 1. Why it exists

Three of the engine's detectors are closed-world classifiers. Each one requires several signals to
agree before it will name a cause:

| Cause | Requires |
|---|---|
| `kb.gap` | KB hit-rate collapses **and** the cohort trails its peers |
| `tool.contract_break` | empty-but-200 responses rise **and** the error rate stays flat **and** resolution drops |
| `prompt.regression` | cost per session rises **and** turns rise **and** resolution stays flat |

That is deliberately strict — it is what keeps false alarms off the report. But it has an obvious
hole: if something real breaks and doesn't match one of those three signatures, a closed-world engine
reports **nothing** and the deployment looks clean.

`unknown` closes that hole. It is the open-world safety net: a resolution drop that is confirmed
against the cohort's own baseline, with no matching signature. The engine would rather say
*"something is wrong here and I can't tell you what"* than stay quiet.

The schema's enum has no `unexplained` member — `unknown` is the value it defines for this case.

---

## 2. When it fires

Only for a cohort that isn't already covered by a classified finding, and only when a downward pulse
clears three gates:

- **it lasts** — at least 2 consecutive weeks; one bad week is a blip, not a regression
- **it clears noise** — bigger than `max(floor, k × noise)`, where noise is the larger of the
  deployment-wide band and *this cohort's own* wobble (a 230-a-week intent swings more on sampling
  alone than a 500-a-week one, and a pooled band is set by the big cohorts)
- **it leaves a baseline** — at least 2 weeks of the series outside the drop, so there's something
  honest to compare against

Those gates are stricter than the classified detectors need, because a stray here is expensive: a
false `is_regression: true` costs specificity points directly *and* lands a false alarm on any decoy
whose window it overlaps.

Practical expectation: **on a clean corpus it should fire zero times.** If you see one on the sealed
run, it is telling you something the three classifiers didn't recognise.

---

## 3. What the screen must do

| Field | Value for `unknown` | What to render |
|---|---|---|
| `findings[].is_regression` | `true` | a normal regression card — it is real |
| `findings[].severity` | `"medium"` | as usual |
| `findings[].impact` | present, with `derivation` | as usual — **but `cost_usd` is `null`** |
| `diagnoses[].cause_class` | `"unknown"` | needs a human-readable label, e.g. *"Cause not identified"* |
| `diagnoses[].confidence` | `0.5` | low by construction; don't present it as certain |
| `diagnoses[].attributed_change` | may be present **or** `null` | ⚠️ see the gotcha below |
| `prescriptions[].change_type` | `"no_action"` | there is no fix — don't render a "apply this change" affordance |
| `prescriptions[].autonomy_rung` | `"L0"` | lowest rung: human-only |
| `prescriptions[].predicted_delta` | `from == to` | ⚠️ don't draw an improvement arrow; nothing is predicted to move |

The APPROVE / REJECT write-back works exactly as it does everywhere else — same `approval` object,
same shape (see `SETUP.md` §4). What the operator is approving is *"yes, someone should go look at
this"*, which the `decision` block already says in words. A REJECT here is a perfectly valid answer
and arguably the more interesting demo beat.

### Three gotchas

1. **`attributed_change` is not a claimed cause.** It's the *nearest config change* to the onset,
   attached for context. On an `unknown` finding the engine is explicitly saying it could not
   classify the cause, so do **not** render it as "caused by the kb change on day 18". Something like
   *"nearest config change: kb, day 18"* is honest; a causal phrasing is not, and this is exactly the
   kind of overclaim the challenge penalises.
2. **`predicted_delta` has `from == to` on purpose.** A no-action prescription predicts no movement.
   A generic "expected improvement" widget will render a meaningless 0% here.
3. **Prescriptions link by `diagnosis_id`, not `finding_id`.** To get from a finding to its
   prescription: `finding.id` → `diagnosis.finding_id` → `diagnosis.id` → `prescription.diagnosis_id`.
   (This is true for every cause class, not just this one — noting it because it's easy to get wrong.)

---

## 4. A real example

Generated from the engine, not hand-written. The numbers are extreme because the drop was injected
synthetically for testing — don't calibrate your layout to them.

```jsonc
// findings[]
{
  "id": "f_unexplained_northwind_retail_return_initiate",
  "tenant": "northwind-retail",
  "cohort": { "intent": "return_initiate" },
  "metric": "resolution_rate",
  "window": { "from_day": 18, "to_day": 55 },
  "observed": 0.0293,
  "expected": 0.7508,
  "is_regression": true,
  "severity": "medium",
  "evidence": [
    "sustained resolution drop 0.751 -> 0.029 over days 21-55",
    "held for 5 consecutive weeks and exceeds this cohort's own noise band (0.016) and the floor (0.120)",
    "no kb, tool, or cost signature matched — cause not classified",
    "nearest config day 18: kb"
  ],
  "impact": {
    "conversations_affected": 3846,
    "share_of_traffic": 0.1413,
    "downstream": { "lost_resolutions_vs_baseline": 2775, "unplanned_handoffs": 3528, "abandoned": 211 },
    "cost_usd": null,
    "days_running": 35,
    "derivation": "3846 sessions of the affected cohort in the post-onset window (14.1% of tenant traffic). Resolution fell 0.751 -> 0.029, so ~2775 resolutions were lost versus the pre-onset baseline; 3528 handoffs and 211 abandons downstream."
  },
  "audience": ["agent_builder", "platform_owner"],
  "if_nothing_changes": "A real, sustained drop with no identified cause — it must be investigated before it is trusted or dismissed."
}

// diagnoses[]
{
  "id": "d_f_unexplained_northwind_retail_return_initiate",
  "finding_id": "f_unexplained_northwind_retail_return_initiate",
  "cause_class": "unknown",
  "confidence": 0.5,
  "attributed_change": { "kind": "kb", "day": 18 },
  "evidence": ["deviation confirmed against the deployment's own baseline and noise band; signature did not match a known cause"]
}

// prescriptions[]
{
  "id": "p_f_unexplained_northwind_retail_return_initiate",
  "diagnosis_id": "d_f_unexplained_northwind_retail_return_initiate",
  "change_type": "no_action",
  "target": "northwind-retail/return_initiate knowledge base",
  "description": "No fix proposed for an unclassified regression — investigate first.",
  "autonomy_rung": "L0",
  "predicted_delta": { "metric": "resolution_rate", "from": 0.0293, "to": 0.0293 },
  "decision": {
    "asking_approval_for": "Investigate the unexplained resolution drop in return_initiate before any change is proposed — the cause is not yet identified.",
    "risk_if_diagnosis_wrong": "Acting on an unclassified drop risks fixing the wrong thing.",
    "would_not_ship_if": "no change is proposed; this asks a human to investigate, not to ship."
  }
}
```

**Note the window vs. the evidence text:** `window.from_day` is 18 but the evidence says the drop ran
days 21–55. That's not a bug — the window is backed up to the nearest config change so the operator
sees the change that preceded the drop. If your card shows both, they will legitimately differ.

---

## 5. The full enum, if you're writing an exhaustive switch

From `nexus-loop-day1/tools/nexus-loop-kit/schema/loop-report.schema.json`:

```
kb.gap · tool.contract_break · tool.outage · prompt.regression · model.change
routing.error · traffic_mix · load · judge_change · unknown
```

The engine emits seven of these: `kb.gap`, `tool.contract_break`, `prompt.regression` (real
regressions), `traffic_mix`, `load`, `judge_change` (decoys — `is_regression: false`, each carrying
`not_a_regression_because`), and `unknown`. The remaining three — `tool.outage`, `model.change`,
`routing.error` — are schema-legal but aren't produced today. A `default:` branch is worth having
anyway.

---

## 6. Producing one locally to test against

The safety net doesn't fire on a clean corpus, so to get a fixture you have to inject a drop that
matches no known signature. The unit test
`engine/tests/test_hardening.py::test_unexplained_fires_on_synthetic_drop` does exactly this. To dump
a full report containing one, save this as a throwaway script at the repo root and run it:

```python
import json
from collections import Counter
from engine import cli, io_corpus, paths

KIT = "nexus-loop-day1/kit"
corpus = paths.corpus_dir(KIT, sample=False)
catalog, labels = paths.load_catalog(KIT), io_corpus.read_labels(KIT)
sessions = io_corpus.read_sessions(corpus)
subs = io_corpus.collect_steps(corpus, ("tool_call", "kb_lookup"))
config_rows = io_corpus.read_config_timeline(corpus)

run = lambda ss: cli.compute_report("solo", "A", catalog, ss, subs["tool_call"],
                                    subs["kb_lookup"], config_rows, labels, ss)

# pick a healthy, high-volume cohort that carries no finding, and break it halfway through
base = run(sessions)
fmap = {f["id"]: f for f in base["findings"]}
taken = {(fmap[d["finding_id"]]["tenant"], fmap[d["finding_id"]]["cohort"].get("intent"))
         for d in base["diagnoses"] if fmap[d["finding_id"]].get("is_regression")}
for (t, i), n in Counter((s["tenant"], s["intent"]) for s in sessions).most_common():
    coh = [s for s in sessions if s["tenant"] == t and s["intent"] == i]
    if (t, i) not in taken and n >= 300 and \
       sum(1 for s in coh if s["session_end"] == "resolved") / len(coh) >= 0.6:
        break
cut = sorted(s["day"] for s in coh)[len(coh) // 2]

flipped = []
for s in sessions:
    c = dict(s)
    if c["tenant"] == t and c["intent"] == i and c["day"] >= cut and c["session_end"] == "resolved":
        c["session_end"] = "handoff"
    flipped.append(c)

with open("engine/out/loop-report-unknown.json", "w") as f:
    json.dump(run(flipped), f, indent=2)
print("wrote engine/out/loop-report-unknown.json")
```

Run it with `python3 <script>.py` from the repo root (that's what puts `engine/` on the import path).
It writes nothing except the report, and `engine/out/` is git-ignored.

The result has **6 findings and 4 prescriptions**: the three real regressions, the `traffic_mix` and
`judge_change` decoys, and one `unknown`. The `load` decoy drops out — the injected drop lands in the
same tenant and breaks the "resolution stayed flat across the spike" condition that makes a load
event a non-regression. That's the fixture being deliberately damaged, not an engine fault.

So: use this file as a **rendering fixture only**. It is not a valid corpus to score against, and the
numbers in the `unknown` card are extreme by construction. For a realistic report, build the normal
one (`SETUP.md` §2) and use this one only to check that the `unknown` card and its no-action
prescription render sensibly.

---

## 7. Why this matters beyond the screen

An engine that only reports what it can name is an engine that goes quiet exactly when something
novel breaks — which is the case the operator most needs to hear about. The `unknown` card is the
system admitting the limit of its own classifier, in public, with the evidence that made it
suspicious and an explicit refusal to propose a fix it can't justify. It should look like a
first-class finding on the screen, not an error state or an empty slot.
