"""The eleven operator asks. Deliberately in business language, deliberately
uneven: some are unambiguous, some have three defensible readings, one is a
Rule 1 trap, one is judged-but-looks-measured, and one is not answerable at all.
"""

ASKS = [
    ("A01", "What share of conversations did we handle end to end without a human?",
     "clean canonical — containment_rate. The only trap is by_design handoffs."),
    ("A02", "Are we getting better or worse month over month?",
     "ambiguous: on which metric, and over which cohort? A mix shift will answer this "
     "wrongly if you do not stratify."),
    ("A03", "Which intents cost us the most per resolved conversation?",
     "measured, but the denominator excludes v2 traffic — state coverage."),
    ("A04", "How often did a tool call fail?",
     "measured. Answering with a model is a Rule 1 violation."),
    ("A05", "Where are people dropping out of the returns journey?",
     "milestone drop-off; needs the journey definition, not a transcript read."),
    ("A06", "Is the knowledge base actually answering what people ask?",
     "measured via kb_hit / kb_top_score — but 'actually answering' has a judged reading too. "
     "Offer both."),
    ("A07", "Did the model upgrade help or hurt?",
     "needs change markers AND a stable cohort. Two model upgrades exist in the corpus; "
     "one is near a judge-version boundary."),
    ("A08", "How much did we spend serving customers this month, and on what?",
     "measured, coverage-limited to v3."),
    ("A09", "How many users gave up out of frustration rather than getting what they "
     "wanted and leaving?",
     "abandonment is MEASURED; the reason is JUDGED. Classifying this as measured is wrong."),
    ("A10", "Which conversations should a human review this week?",
     "open — a good answer composes a deterministic scope with a judged score."),
    ("A11", "What is our failover rate — how often did a primary model or tool fail and "
     "an alternate serve the user instead?",
     "NOT MEASURABLE. No failover mechanism exists. The correct output is a gap spec."),
]


def render() -> str:
    out = ["# The asks", "",
           "Eleven questions, in the words an operator would actually use. Your system takes",
           "these as input. They are not all answerable, and they are not all answerable the",
           "same way — that is the point.", "",
           "Nothing here tells you which is which. The notes below are the *shape* of the ask,",
           "not the answer.", ""]
    for aid, q, shape in ASKS:
        out += ["## %s" % aid, "", "> %s" % q, "", "_Shape:_ %s" % shape, ""]
    return "\n".join(out)
