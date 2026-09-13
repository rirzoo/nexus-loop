"""The session-grain re-aggregation guard, proven on real sample data.

The single most common error against this corpus is averaging a step-grain measure
across sessions without first collapsing to one value per session. This test shows
the two paths give materially different answers, so the correct path is not academic.
"""
import os
import unittest

from engine import aggregate, io_corpus, paths

KIT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                    "nexus-loop-day1", "kit"))


class ReaggregationTrap(unittest.TestCase):
    def setUp(self):
        corpus = paths.corpus_dir(KIT, sample=True)
        self.steps = io_corpus.collect_steps(corpus, ("tool_call",))["tool_call"]

    def test_step_average_differs_from_session_grain(self):
        """'error share over all tool steps' (step grain) vs 'mean over sessions of
        each session's own error share' (session grain) are different numbers.
        Sessions with many steps dominate the first; each session counts once in the
        second. If these ever coincide the trap is hidden, so assert they diverge."""
        is_err = lambda s: s["outcome"] in ("error", "timeout")

        # step-grain: pool every step, one big ratio
        step_rate, _, den = aggregate.rate_over_steps(self.steps, is_err)
        self.assertGreater(den, 0)

        # session-grain: collapse to one error-share per session, then average
        per_session = aggregate.reaggregate_to_session(
            self.steps,
            lambda rows: sum(1 for r in rows if is_err(r)) / len(rows))
        session_rate = aggregate.mean(per_session.values())

        # they must not be identical — that's the whole point of the guard
        self.assertNotAlmostEqual(step_rate, session_rate, places=4)

    def test_reaggregate_collapses_to_one_value_per_session(self):
        per_session = aggregate.reaggregate_to_session(self.steps, lambda rows: len(rows))
        distinct_sessions = {s["session_id"] for s in self.steps}
        self.assertEqual(set(per_session), distinct_sessions)
        self.assertEqual(sum(per_session.values()), len(self.steps))


if __name__ == "__main__":
    unittest.main()
