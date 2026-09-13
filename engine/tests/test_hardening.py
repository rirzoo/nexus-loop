"""Sealed-run hardening harness.

The sealed corpus plants the same KINDS of problems on DIFFERENT days, tenants, and
slices of traffic, and the engine must run untouched. We cannot see it, so the only
honest way to validate robustness is to apply those exact transforms to the practice
corpus and assert the faults are still found and correctly attributed — with no
hand-tuning between runs.

Transforms:
  - day_shift:     add a constant to every day (+ config timeline) — nothing may key on
                   an absolute day; attribution must track the shift.
  - tenant_rename: remap tenant names everywhere — nothing may key on a tenant name.
  - subsample:     thin traffic deterministically — volume guards must not drop a real
                   fault at reduced volume.

Ground truth is NOT used: assertions are by cause_class and the cohort identities we
already know from the practice data, exactly what must survive to the sealed corpus.
"""
import os
import unittest
import zlib

from engine import cli, io_corpus, paths

KIT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                    "nexus-loop-day1", "kit"))

REAL = {"kb.gap", "tool.contract_break", "prompt.regression"}
DECOY = {"traffic_mix", "load", "judge_change"}
# the config days each real fault is attributed to in the practice corpus
ATTR_DAY = {"kb.gap": 34, "tool.contract_break": 40, "prompt.regression": 46}


def _rows(rows, **overrides):
    """Shallow-copy each row, overriding top-level fields via callables on the row."""
    out = []
    for r in rows:
        c = dict(r)
        for k, fn in overrides.items():
            c[k] = fn(r)
        out.append(c)
    return out


class SealedRunHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus = paths.corpus_dir(KIT, sample=False)
        cls.catalog = paths.load_catalog(KIT)
        cls.sessions = io_corpus.read_sessions(corpus)
        subsets = io_corpus.collect_steps(corpus, ("tool_call", "kb_lookup"))
        cls.tool_calls = subsets["tool_call"]
        cls.kb_lookups = subsets["kb_lookup"]
        cls.config_rows = io_corpus.read_config_timeline(corpus)
        cls.labels = io_corpus.read_labels(KIT)

    def _run(self, sessions, tool_calls, kb_lookups, config_rows):
        return cli.compute_report("solo", "A", self.catalog, sessions, tool_calls,
                                  kb_lookups, config_rows, self.labels, sessions)

    def _causes(self, rep):
        fmap = {f["id"]: f for f in rep["findings"]}
        real, decoy, attr = set(), set(), {}
        for d in rep["diagnoses"]:
            f = fmap[d["finding_id"]]
            cc = d["cause_class"]
            if f.get("is_regression"):
                real.add(cc)
                if d.get("attributed_change"):
                    attr[cc] = d["attributed_change"]["day"]
            else:
                decoy.add(cc)
        return real, decoy, attr

    def _assert_all_found(self, rep, msg):
        real, decoy, _ = self._causes(rep)
        self.assertTrue(REAL <= real, "%s: missing real faults %s" % (msg, REAL - real))
        self.assertTrue(DECOY <= decoy, "%s: missing decoys %s" % (msg, DECOY - decoy))

    def test_baseline(self):
        rep = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        self._assert_all_found(rep, "baseline")

    def test_day_shift_tracks_attribution(self):
        off = 90  # arbitrary large offset
        s = _rows(self.sessions, day=lambda r: r["day"] + off)
        tc = _rows(self.tool_calls, day=lambda r: r["day"] + off)
        kb = _rows(self.kb_lookups, day=lambda r: r["day"] + off)
        cfg = _rows(self.config_rows, day=lambda r: r["day"] + off)
        rep = self._run(s, tc, kb, cfg)
        self._assert_all_found(rep, "day_shift")
        _, _, attr = self._causes(rep)
        for cc, base_day in ATTR_DAY.items():
            self.assertEqual(attr.get(cc), base_day + off,
                             "day_shift: %s attributed to %s, expected %d"
                             % (cc, attr.get(cc), base_day + off))

    def test_tenant_rename(self):
        m = {"acme-bank": "alpha-corp", "northwind-retail": "beta-mart"}
        ren = lambda r: m.get(r["tenant"], r["tenant"])  # '*' and others pass through
        s = _rows(self.sessions, tenant=ren)
        tc = _rows(self.tool_calls, tenant=ren)
        kb = _rows(self.kb_lookups, tenant=ren)
        cfg = _rows(self.config_rows, tenant=ren)
        rep = self._run(s, tc, kb, cfg)
        self._assert_all_found(rep, "tenant_rename")
        # findings must carry the NEW names, proving no hardcoded tenant leaked through
        names = {f["tenant"] for f in rep["findings"]}
        self.assertFalse({"acme-bank", "northwind-retail"} & names,
                         "tenant_rename: old tenant name leaked into findings")

    def test_subsample_half(self):
        # crc32 is stable across processes (unlike salted hash()), so the slice is
        # reproducible — a flaky volume test would hide a real regression.
        keep = lambda sid: (zlib.crc32(sid.encode()) % 2 == 0)
        s = [r for r in self.sessions if keep(r["session_id"])]
        ids = {r["session_id"] for r in s}
        tc = [r for r in self.tool_calls if r["session_id"] in ids]
        kb = [r for r in self.kb_lookups if r["session_id"] in ids]
        rep = self._run(s, tc, kb, self.config_rows)
        self._assert_all_found(rep, "subsample_half")


if __name__ == "__main__":
    unittest.main()
