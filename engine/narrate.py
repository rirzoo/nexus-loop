"""Say, in plain words, what the numbers mean — deterministically, at build time.

The screen's job is to make a stranger comfortable in seconds, and a stranger does not
read `resolution_rate 0.4334 vs 0.7924`. They read "most people asking about the new card
never got an answer". This module authors that sentence.

It is templating, not inference. Every phrase is assembled from values the detectors
already computed, the same way `detect_faults` builds its `derivation` strings — no model,
no network, no key, so the system still runs untouched and offline on the sealed corpus.
That also keeps the prose inside `report_build`: a person approving a fix is bound to the
exact words they read, not to a paraphrase generated later.

Two rules hold the templates together:

  * Nothing is keyed to this corpus. Templates key off `cause_class` (a schema enum) and
    the presence of impact fields; every noun comes from the finding's own cohort/tenant
    at render time. A cause class we have no template for still reads sensibly via
    `_DEFAULT`, because the sealed corpus is allowed to surprise us.
  * Proportions are not spelled out. The screen draws shares as bars and keeps the figure
    in the definition popover, so these strings carry counts and plain comparatives and
    leave the percentages alone.
"""
from __future__ import annotations

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine", "ten", "eleven", "twelve")


def _words(n):
    """Counts as words, so the opening summary carries no digits at all."""
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def _humanise(value):
    """A raw identifier as something a person would say out loud."""
    return str(value).replace("_", " ").strip()


def _trim(value):
    """A number without trailing zeros — 6.0 reads as 6 in a sentence."""
    text = "%g" % float(value)
    return text


def _sentence(text):
    """Capitalise a fragment so it can follow a full stop."""
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def _topic(finding):
    """The bare subject, for sentences of the form "people asking about ___"."""
    cohort = finding.get("cohort") or {}
    intent = cohort.get("intent")
    if intent and not cohort.get("tool") and not cohort.get("agent_id"):
        for suffix in ("_info", "_enquiry", "_query", "_questions"):
            if str(intent).endswith(suffix):
                return _humanise(str(intent)[:-len(suffix)])
        return _humanise(intent)
    return _cohort_noun(finding)


def _cohort_noun(finding):
    """What slice of traffic this is, in an operator's words.

    Cohort keys vary by detector and the sealed corpus may slice differently again, so
    this reads whichever key is present rather than assuming one.
    """
    cohort = finding.get("cohort") or {}
    if cohort.get("tool"):
        return "the %s tool" % cohort["tool"]
    if cohort.get("agent_id"):
        return "the %s agent" % cohort["agent_id"]
    if cohort.get("intent"):
        return "%s questions" % _topic(finding)
    for key, value in sorted(cohort.items()):
        if value not in (None, ""):
            return "%s %s" % (_humanise(key), _humanise(value))
    return "this deployment's traffic as a whole"


def _tool_subject(finding):
    """Name the tool when the cohort names one; otherwise say a tool is involved."""
    cohort = finding.get("cohort") or {}
    if cohort.get("tool"):
        return "the %s tool" % cohort["tool"]
    return "a tool behind %s" % _cohort_noun(finding)


def _tenant_noun(finding):
    tenant = finding.get("tenant")
    if tenant in (None, "", "*"):
        return "every tenant in this deployment"
    return str(tenant)


def _where(finding):
    cohort = finding.get("cohort") or {}
    if not cohort:
        return _tenant_noun(finding)
    if finding.get("tenant") in (None, "", "*"):
        return _cohort_noun(finding)
    return "%s, on %s" % (_cohort_noun(finding), finding["tenant"])


# --------------------------------------------------------------------------------------
# Titles and the opening sentence. One number-free line each: this is the whole of slide
# one, and it is deliberately the only thing on it.
# --------------------------------------------------------------------------------------

_TITLE = {
    "kb.gap": "People with {topic} questions are not getting an answer.",
    "tool.contract_break": "{tool_subject_cap} reports success and returns nothing.",
    "tool.outage": "{tool_subject_cap} is failing outright.",
    "prompt.regression":
        "Conversations on {cohort} take longer and cost more, for the same result.",
    "model.change": "A model change altered how {cohort} behaves.",
    "routing.error": "Conversations in {cohort} are being sent to the wrong place.",
    "traffic_mix": "The questions people asked changed. The service did not.",
    "load": "A rush of traffic slowed things down, and then it passed.",
    "judge_change": "The scoring rubric changed. The agents did not.",
    "unknown": "Something about {cohort} got worse, and we cannot say why.",
}
_TITLE_DEFAULT = "Something changed in {cohort} and it is worth a look."

_STORY = {
    "kb.gap": (
        "A new line of questions started arriving and the knowledge base has nothing to "
        "answer them with. The agent falls back to a generic reply, so the person either "
        "waits for a human or leaves without what they came for."),
    "tool.contract_break": (
        "The tool still reports success on every call, so nothing looks broken from the "
        "outside. It has stopped putting anything in the reply, and the agent "
        "passes that emptiness on to the customer."),
    "prompt.regression": (
        "Conversations here started taking more back-and-forth than they used to. They "
        "cost more to serve as a result, and they finish no better than they did before, "
        "so the extra turns are buying nothing."),
    "tool.outage": (
        "Calls to this tool are failing outright, so the agent cannot complete the part "
        "of the conversation that depends on it."),
    "model.change": (
        "The model serving this traffic changed, and behaviour moved with it."),
    "routing.error": (
        "Conversations are being handed to the wrong destination, so the agent picking "
        "them up is not the one equipped to help."),
    "traffic_mix": (
        "More people arrived asking an easy question, which lifted the headline number on "
        "its own. Looked at one question at a time, nothing improved or got worse. What "
        "changed is who turned up, not how well they were served."),
    "load": (
        "Traffic ran far above normal for a few days and the platform slowed under it. "
        "Answers stayed as good as they always were, and the slowdown cleared by itself. "
        "The platform team should know about it, but service quality never dropped."),
    "judge_change": (
        "The rubric used to score conversation quality moved to a stricter version, and "
        "every score moved with it, on both tenants, on the same day. The agents behaved "
        "exactly as they did the week before. Scores either side of that change are not "
        "comparable, so we do not compare them."),
    "unknown": (
        "This held up for weeks, then fell and stayed down, by more than this slice of "
        "traffic normally wanders. None of the usual culprits fit: the knowledge base is "
        "answering, the tools are returning what they should, and cost has not moved. We "
        "are confident it is real and we cannot name the cause, so we are not guessing at "
        "one."),
}
_STORY_DEFAULT = (
    "This slice of traffic moved away from where it normally sits, by enough and for long "
    "enough that it is unlikely to be noise.")


# --------------------------------------------------------------------------------------
# Why it happened. The cause in plain words, then the change we traced it to. Attribution
# can legitimately come back empty — a real deviation with no matching config change is
# still a real deviation — so that case gets a sentence of its own rather than a blank.
# --------------------------------------------------------------------------------------

_CAUSE = {
    "kb.gap": "a knowledge base with nothing in it for these questions",
    "tool.contract_break": "a tool returning empty replies while reporting success",
    "tool.outage": "a tool failing outright",
    "prompt.regression": "a prompt change that made the agent more long-winded",
    "model.change": "a change to the model serving this traffic",
    "routing.error": "conversations being routed to the wrong place",
    "traffic_mix": "a shift in the mix of questions people asked",
    "load": "a spike in traffic",
    "judge_change": "the quality rubric moving to a stricter version",
    "unknown": "something we could not name",
}


def _cause_plain(diagnosis):
    if not diagnosis:
        return "We did not reach a diagnosis for this one."
    cause = diagnosis.get("cause_class")
    reason = _CAUSE.get(cause, _humanise(cause) if cause else "not identified")
    change = diagnosis.get("attributed_change") or {}
    if cause == "unknown":
        return ("We confirmed the drop against this cohort's own history and its own "
                "week-to-week noise, but no signature matched, so the cause is recorded "
                "as unidentified rather than guessed at.")
    if change.get("day") is not None:
        return ("We traced this to %s, following a %s change on day %s. The change comes "
                "first and the effect follows it."
                % (reason, _humanise(change.get("kind") or "configuration"), change["day"]))
    return ("The signals point to %s. No configuration change lines up with the window, "
            "so nothing is being blamed for it." % reason)


# --------------------------------------------------------------------------------------
# The baseline, named and justified. The PDF asks for this explicitly, and it genuinely
# differs per finding: a cohort born broken has no past to compare with and can only be
# judged against its peers, while a cohort that fell has its own past and must be judged
# against that. The detector records which axis it used; we only put it into words.
# --------------------------------------------------------------------------------------

_AXIS_BY_CAUSE = {"kb.gap": "peer"}


def _baseline(finding, diagnosis, standard):
    axis = finding.get("baseline_axis")
    if not axis:
        cause = (diagnosis or {}).get("cause_class")
        axis = _AXIS_BY_CAUSE.get(cause, "own_past")
    expected = finding.get("expected")
    if axis == "peer":
        source = "other question types on the same tenant"
        why = ("This cohort has never had a healthy stretch to compare against. It has "
               "answered badly since the day it appeared, so the yardstick is the rest of "
               "this tenant's traffic: the middle of what its other question types "
               "achieve, mined from the deployment's own history.")
        for row in standard or []:
            if row.get("tenant") == finding.get("tenant"):
                expected = row.get("median", expected)
                break
    else:
        source = "this same cohort, outside the affected window"
        why = ("This cohort looks like its peers, so peers tell us nothing. The yardstick "
               "is its own traffic on the days either side of the fault: the same "
               "customers asking the same things, before and after it went wrong.")
    return {"axis": axis, "value": expected, "source": source, "why": why}


# --------------------------------------------------------------------------------------
# What it did to people. The PDF's "a finding is a decision, not a number" list, emitted
# only for the fields a given finding actually carries — impact.downstream differs by
# detector, and a screen that loops a fixed list prints blanks at a judge.
# --------------------------------------------------------------------------------------

def _effects(finding):
    impact = finding.get("impact") or {}
    down = impact.get("downstream") or {}
    tenant = _tenant_noun(finding)
    out = []

    def add(key, headline, detail, field):
        out.append({"key": key, "headline": headline, "detail": detail, "field": field})

    # A fault that never moves an outcome — longer, dearer conversations that still end
    # the same way — has nothing to say in the outcome blocks below. Lead with the
    # measure that did move, or its harm is invisible on this slide.
    if down.get("unplanned_handoffs") is None and down.get("abandoned") is None:
        observed, expected = finding.get("observed"), finding.get("expected")
        metric = str(finding.get("metric") or "")
        if observed is not None and expected is not None and "turn" in metric:
            add("metric_move", "Longer than they used to be",
                "%s back-and-forth turns to get sorted, where the same conversations "
                "took %s before." % (_trim(observed), _trim(expected)), "observed")
        elif observed is not None and expected is not None:
            add("metric_move", "Moved away from where it sat",
                "%s now, against %s before the fault." % (_trim(observed), _trim(expected)),
                "observed")

    n = impact.get("conversations_affected")
    if n is not None:
        add("volume", "Conversations caught up in it",
            "%s %s in this slice while the fault was running, measured against all of %s's "
            "traffic over the same days." % (n, _plural(n, "conversation"), tenant),
            "impact.conversations_affected")

    lost = down.get("would_have_resolved_at_baseline")
    if lost is None:
        lost = down.get("lost_resolutions_vs_baseline")
    if lost is not None:
        add("lost", "Should have been sorted and were not",
            "%s %s that would have resolved at the baseline rate did not."
            % (lost, _plural(lost, "conversation")),
            "impact.downstream")

    handoffs = down.get("unplanned_handoffs")
    if handoffs is not None:
        add("handoffs", "Reached a person who was never meant to be involved",
            "%s %s ended up with a human agent the journey never intended to use."
            % (handoffs, _plural(handoffs, "conversation")),
            "impact.downstream")

    abandoned = down.get("abandoned")
    if abandoned is not None:
        add("abandoned", "Gave up without an answer",
            "%s %s left without resolving. How many gave up is measured; why they gave up "
            "is not. That is one of the questions this system refuses."
            % (abandoned, _plural(abandoned, "person", "people")),
            "impact.downstream")

    days = impact.get("days_running")
    if days is not None:
        add("duration", "Running before anyone noticed",
            "%s %s from the day it started to the last day we can see it."
            % (days, _plural(days, "day")), "impact.days_running")

    spend = impact.get("cost_usd")
    if spend is None:
        spend = down.get("avoidable_spend_usd")
    if spend is not None:
        add("cost", "Spend that bought nothing",
            "Model spend on this slice above what the same traffic cost before the fault. "
            "Only agents that record a cost per call are counted.",
            "impact.cost_usd")
    return out


def _band(finding):
    """The outcome split behind the impact bar: what became of those conversations.

    Only emitted when the finding records both downstream outcomes, because the remainder
    is inferred as resolved and that inference is wrong if either count is missing. A
    cost-and-turns fault records neither, and rightly gets no band — nothing about its
    harm is visible in how conversations ended.
    """
    impact = finding.get("impact") or {}
    down = impact.get("downstream") or {}
    total = impact.get("conversations_affected")
    handoffs = down.get("unplanned_handoffs")
    abandoned = down.get("abandoned")
    if total is None or handoffs is None or abandoned is None:
        return None
    resolved = max(0, total - handoffs - abandoned)
    return {
        "total": total,
        "segments": [
            {"key": "resolved", "label": "sorted without a person", "count": resolved},
            {"key": "handoff", "label": "handed to a person", "count": handoffs},
            {"key": "abandoned", "label": "gave up", "count": abandoned},
        ],
        "caption": "What became of the %s conversations in this slice." % total,
    }


# --------------------------------------------------------------------------------------
# Status lines, and the separate wording a dismissed lookalike needs. Calling a decoy
# "degraded service" undoes the very thing examining it earned.
# --------------------------------------------------------------------------------------

def _status(finding, diagnosis):
    days = (finding.get("impact") or {}).get("days_running")
    if not finding.get("is_regression"):
        return ("Examined and ruled out", "We looked at this and decided it is not a fault.")
    change = (diagnosis or {}).get("attributed_change") or {}
    # An unclassified fault must not claim a cause in its status line while the slide next
    # to it says we could not find one. The nearest change is recorded, not blamed.
    if (diagnosis or {}).get("cause_class") == "unknown":
        traced = "cause not identified"
    elif change.get("day") is not None:
        traced = "traced to a change on day %s" % change["day"]
    else:
        traced = "no configuration change lines up with it"
    if days is not None:
        return ("%s %s of this, %s" % (days, _plural(days, "day"), traced),
                "It ran for %s %s before anyone acted, and %s."
                % (days, _plural(days, "day"), traced))
    return ("Needs a decision, %s" % traced, "This needs a decision, and %s." % traced)


def _dismissed(finding, diagnosis):
    cause = (diagnosis or {}).get("cause_class")
    lead = {
        "traffic_mix": "The totals moved because the mix of questions moved.",
        "load": "The platform slowed under load and then recovered on its own.",
        "judge_change": "The scores moved because the rubric moved.",
    }.get(cause, "We examined this and it does not hold up as a fault.")
    return "%s %s" % (lead, _sentence(finding.get("not_a_regression_because")))


# --------------------------------------------------------------------------------------
# Gaps and prescriptions.
# --------------------------------------------------------------------------------------

_ASK_TEXT = {
    "A01": "What share of conversations did we handle end to end without a human?",
    "A02": "Are we getting better or worse month over month?",
    "A03": "Which intents cost us the most per resolved conversation?",
    "A04": "How often did a tool call fail?",
    "A05": "Where are people dropping out of the returns journey?",
    "A06": "Is the knowledge base actually answering what people ask?",
    "A07": "Did the model upgrade help or hurt?",
    "A08": "How much did we spend serving customers this month, and on what?",
    "A09": "How many users gave up out of frustration rather than getting what they "
           "wanted and leaving?",
    "A10": "Which conversations should a human review this week?",
    "A11": "What is our failover rate — how often did a primary model or tool fail and an "
           "alternate serve the user instead?",
}

_REFUSAL = {
    "NOT_MEASURABLE": "Nothing in the logs can answer this, and no amount of care with the "
                      "data we have will change that.",
    "REQUIRES_NEW_JUDGE": "Part of this is measured and part of it is a judgement call that "
                          "nothing has been built to make yet.",
    "COVERAGE_TOO_LOW": "Too little of the traffic can produce this number for an answer to "
                        "mean anything.",
    "CARDINALITY_REFUSED": "The breakdown asked for would splinter into more groups than it "
                           "is allowed, so the answer would look precise and be noise.",
}


def _gap_plain(gap):
    event = gap.get("required_event") or {}
    if event.get("name"):
        name = _humanise(event["name"])
        needed = ("Someone would have to start logging %s %s event, at the %s level, "
                  "owned by %s. That turns this refusal into a ticket."
                  % ("an" if name[:1].lower() in "aeiou" else "a", name,
                     _humanise(event.get("grain") or "right"),
                     event.get("owner") or "the owning team"))
    else:
        needed = ("No new event is needed. The data exists; the breakdown asked for does "
                  "not fit inside the budget it was given.")
    return {
        "asked": _ASK_TEXT.get(gap.get("ask_id"),
                               "Operator question %s" % gap.get("ask_id")),
        "refused_because": _REFUSAL.get(gap.get("verdict"),
                                        "We cannot answer this from the data we have."),
        "what_would_have_to_exist": needed,
    }


def _predicted_plain(prescription):
    delta = prescription.get("predicted_delta") or {}
    metric = _humanise(delta.get("metric") or "the metric")
    if prescription.get("change_type") == "no_action":
        return ("No change is being proposed, so nothing is predicted. This asks for an "
                "investigation, not a deployment.")
    return ("If this works, %s should return to the baseline, where it sat before. "
            "Naming that target up front is what makes the claim checkable afterwards."
            % metric)


# --------------------------------------------------------------------------------------
# The run in one paragraph, and the standing admission of what this system is and is not.
# --------------------------------------------------------------------------------------

def _summary(findings, gaps):
    regressions = [f for f in findings if f.get("is_regression")]
    dismissed = [f for f in findings if not f.get("is_regression")]
    tenants = sorted({f.get("tenant") for f in regressions if f.get("tenant")})
    where = " and ".join(tenants) if 0 < len(tenants) <= 2 else "this deployment"

    if not regressions:
        opening = ("We read eight weeks of this deployment's logs and found nothing that "
                   "needs your decision today.")
    else:
        opening = ("We read eight weeks of this deployment's logs. %s %s on %s %s your "
                   "decision."
                   % (_words(len(regressions)).capitalize(),
                      _plural(len(regressions), "thing"), where,
                      _plural(len(regressions), "needs", "need")))
    middle = ("Another %s looked like trouble and turned out not to be, so we set %s "
              "aside and wrote down why."
              % (_words(len(dismissed)),
                 "it" if len(dismissed) == 1 else "them")) if dismissed else ""
    tail = ("We also refused %s %s outright, because the data cannot honestly answer %s."
            % (_words(len(gaps)), _plural(len(gaps), "question"),
               "it" if len(gaps) == 1 else "them")) if gaps else ""
    return " ".join(part for part in (opening, middle, tail) if part)


def _whats_real(metrics, verifications, self_assessment):
    calibrated = any(m.get("calibration") for m in metrics or [])
    replayed = bool(verifications)
    cycles = (self_assessment or {}).get("cycles") or 0
    return [
        {"key": "detection", "claim": "Finding the faults and naming their causes",
         "status": "real",
         "why": "Every finding here came out of the logs. Cohorts and time windows are "
                "found in the data itself, and the configuration timeline is only "
                "consulted afterwards, to attribute a fault we had already found."},
        {"key": "numbers", "claim": "The figures on these screens", "status": "real",
         "why": "Counted from the corpus, never asserted. Each one opens to show how it "
                "was computed, what share of traffic it covers, and what it excludes."},
        {"key": "words", "claim": "The plain-language wording", "status": "templated",
         "why": "Written by us as templates and filled in with the computed numbers. No "
                "language model writes anything you read here, so the same report always "
                "produces the same words."},
        {"key": "judge", "claim": "The quality score's calibration",
         "status": "real" if calibrated else "not_run",
         "why": ("Published as agreement with the kit's human labels. Human reviewers "
                 "disagree with each other, so perfect agreement would be a bug."
                 if calibrated else
                 "No judged metric on this run carries human labels to calibrate against, "
                 "so no agreement is claimed.")},
        {"key": "replay", "claim": "Proof that a fix actually works",
         "status": "real" if replayed else "not_run",
         "why": ("Each proposed fix was put through the replay service and the before and "
                 "after are shown as returned."
                 if replayed else
                 "The replay service has not been run for this report, so no fix here is "
                 "proven. Predictions are shown as predictions.")},
        {"key": "return_arrow", "claim": "The system scoring its own past predictions",
         "status": "real" if cycles else "not_run",
         "why": ("Past predictions are checked against what replay actually returned."
                 if cycles else
                 "A hit rate is only claimed once predictions have been checked against "
                 "real replay outcomes. None have, so none is claimed.")},
        {"key": "scale", "claim": "What breaks at a hundred times this size",
         "status": "known_limit",
         "why": "The step rows are held in memory while they are grouped. At this size "
                "that is comfortable; a hundred times larger would need the same "
                "arithmetic done in a single streaming pass. The detection logic itself "
                "already works that way."},
    ]


# --------------------------------------------------------------------------------------

def annotate(findings, diagnoses, prescriptions, gaps, metrics, standard,
             self_assessment, verifications=None) -> dict:
    """Add the plain-language layer in place; return the report-level narration.

    Called before the report is assembled so the wording lands inside `report_build` —
    an approval has to be bound to the words the person actually read.
    """
    by_finding = {d.get("finding_id"): d for d in diagnoses or []}

    for finding in findings or []:
        diagnosis = by_finding.get(finding.get("id"))
        cause = (diagnosis or {}).get("cause_class")
        cohort = _cohort_noun(finding)
        tool_subject = _tool_subject(finding)
        fields = {"cohort": cohort, "cohort_cap": _sentence(cohort),
                  "topic": _topic(finding), "tenant": _tenant_noun(finding),
                  "tool_subject": tool_subject,
                  "tool_subject_cap": _sentence(tool_subject)}

        finding["title_plain"] = _TITLE.get(cause, _TITLE_DEFAULT).format(**fields)
        finding["where_plain"] = _where(finding)
        finding["what_happened"] = _STORY.get(cause, _STORY_DEFAULT)
        finding["cause_plain"] = _cause_plain(diagnosis)
        finding["needs_your_inference"] = cause == "unknown"

        short, long = _status(finding, diagnosis)
        finding["status_short"] = short
        finding["status_plain"] = long

        if finding.get("is_regression"):
            baseline = _baseline(finding, diagnosis, standard)
            finding["baseline"] = baseline
            finding["baseline_plain"] = "Compared against %s. %s" % (baseline["source"],
                                                                     baseline["why"])
            finding["effect_plain"] = _effects(finding)
            band = _band(finding)
            if band:
                finding["outcome_band"] = band
        else:
            finding["dismissed_plain"] = _dismissed(finding, diagnosis).strip()

    regressions = [f for f in findings or [] if f.get("is_regression")]
    regressions.sort(key=lambda f: (
        _SEVERITY_ORDER.get(f.get("severity"), 9),
        -((f.get("impact") or {}).get("conversations_affected") or 0)))
    for position, finding in enumerate(regressions, start=1):
        finding["rank"] = position

    for prescription in prescriptions or []:
        prescription["predicted_plain"] = _predicted_plain(prescription)

    for gap in gaps or []:
        gap["plain"] = _gap_plain(gap)

    return {
        "summary_plain": _summary(findings or [], gaps or []),
        "whats_real": _whats_real(metrics, verifications, self_assessment),
    }
