# The asks

Eleven questions, in the words an operator would actually use. Your system takes
these as input. They are not all answerable, and they are not all answerable the
same way — that is the point.

Nothing here tells you which is which. The notes below are the *shape* of the ask,
not the answer.

## A01

> What share of conversations did we handle end to end without a human?

_Shape:_ clean canonical — containment_rate. The only trap is by_design handoffs.

## A02

> Are we getting better or worse month over month?

_Shape:_ ambiguous: on which metric, and over which cohort? A mix shift will answer this wrongly if you do not stratify.

## A03

> Which intents cost us the most per resolved conversation?

_Shape:_ measured, but the denominator excludes v2 traffic — state coverage.

## A04

> How often did a tool call fail?

_Shape:_ measured. Answering with a model is a Rule 1 violation.

## A05

> Where are people dropping out of the returns journey?

_Shape:_ milestone drop-off; needs the journey definition, not a transcript read.

## A06

> Is the knowledge base actually answering what people ask?

_Shape:_ measured via kb_hit / kb_top_score — but 'actually answering' has a judged reading too. Offer both.

## A07

> Did the model upgrade help or hurt?

_Shape:_ needs change markers AND a stable cohort. Two model upgrades exist in the corpus; one is near a judge-version boundary.

## A08

> How much did we spend serving customers this month, and on what?

_Shape:_ measured, coverage-limited to v3.

## A09

> How many users gave up out of frustration rather than getting what they wanted and leaving?

_Shape:_ abandonment is MEASURED; the reason is JUDGED. Classifying this as measured is wrong.

## A10

> Which conversations should a human review this week?

_Shape:_ open — a good answer composes a deterministic scope with a judged score.

## A11

> What is our failover rate — how often did a primary model or tool fail and an alternate serve the user instead?

_Shape:_ NOT MEASURABLE. No failover mechanism exists. The correct output is a gap spec.
