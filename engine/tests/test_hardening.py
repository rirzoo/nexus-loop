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
import csv
import json
import os
import shutil
import tempfile
import unittest
import zlib
from collections import Counter

from engine import cli, io_corpus, paths, preflight

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


def _keep_frac(sid, pct):
    """Deterministic across processes (crc32, not salted hash()): keep ~pct of ids."""
    return (zlib.crc32(sid.encode()) % 1000) < int(pct * 1000)


def _subsample(sessions, tool_calls, kb_lookups, pct, tenant=None):
    """Thin sessions to ~pct (optionally only within one tenant) and carry the matching
    steps. Reproducible, so a volume test can never flake and hide a real regression."""
    def keep(s):
        if tenant is not None and s["tenant"] != tenant:
            return True  # leave other tenants at full volume
        return _keep_frac(s["session_id"], pct)
    s = [r for r in sessions if keep(r)]
    ids = {r["session_id"] for r in s}
    tc = [r for r in tool_calls if r["session_id"] in ids]
    kb = [r for r in kb_lookups if r["session_id"] in ids]
    return s, tc, kb


def _cohort_session_ids(sessions, finding):
    """session_ids belonging to a finding's cohort (by tenant + intent/agent_id)."""
    t, c = finding["tenant"], finding["cohort"]
    out = set()
    for s in sessions:
        if s["tenant"] != t:
            continue
        if "intent" in c and s.get("intent") != c["intent"]:
            continue
        if "agent_id" in c and s.get("agent_id") != c["agent_id"]:
            continue
        out.add(s["session_id"])
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

    def test_window_starts_at_or_before_onset(self):
        # The accuracy squeeze: a finding's window must start no later than the fault's
        # onset (== its attributed change day here), so the scorer charges zero detection
        # lag. A regression that pushed the window start later would silently cost points.
        rep = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        fmap = {f["id"]: f for f in rep["findings"]}
        for d in rep["diagnoses"]:
            cc = d["cause_class"]
            if cc in ATTR_DAY:
                fd = (fmap[d["finding_id"]].get("window") or {}).get("from_day")
                self.assertIsNotNone(fd, "%s finding has no window.from_day" % cc)
                self.assertLessEqual(
                    fd, ATTR_DAY[cc],
                    "%s window starts day %s, after onset %d — this reintroduces "
                    "detection lag" % (cc, fd, ATTR_DAY[cc]))

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

    # ---- Part A: robustness harness (measurement + invariants) ----

    def _real_findings(self, rep):
        """cause_class -> the real finding, for the cohort-aware transforms below."""
        fmap = {f["id"]: f for f in rep["findings"]}
        out = {}
        for d in rep["diagnoses"]:
            f = fmap[d["finding_id"]]
            if f.get("is_regression"):
                out[d["cause_class"]] = f
        return out

    def test_volume_sweep(self):
        # Turn "3/3 at one volume" into a measured margin: how thin can traffic get
        # before a real fault drops below the (unchanged) volume gates? Nothing is
        # tuned between runs — this only observes the agnostic engine at lower volume.
        survive = {}
        for pct in (0.5, 0.33, 0.25, 0.15):
            s, tc, kb = _subsample(self.sessions, self.tool_calls, self.kb_lookups, pct)
            real, _, _ = self._causes(self._run(s, tc, kb, self.config_rows))
            survive[pct] = real & REAL
            print("volume %2.0f%%: %d/%d real faults -> %s"
                  % (pct * 100, len(survive[pct]), len(REAL), sorted(survive[pct])))
        self.assertEqual(survive[0.5], REAL, "a real fault dropped at 50%% volume")
        for cc in sorted(REAL):
            floors = [p for p in sorted(survive) if cc in survive[p]]
            print("  margin[%s]: survives to %s"
                  % (cc, ("%.0f%%" % (min(floors) * 100)) if floors else ">50%"))

    def test_thin_one_tenant(self):
        base = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        for cc, f in self._real_findings(base).items():
            tenant = f["tenant"]
            s, tc, kb = _subsample(self.sessions, self.tool_calls, self.kb_lookups,
                                   0.5, tenant=tenant)
            real, _, _ = self._causes(self._run(s, tc, kb, self.config_rows))
            print("thin %s to 50%%: %s found=%s" % (tenant, cc, cc in real))
            self.assertIn(cc, real,
                          "%s lost when its tenant %s thinned to 50%%" % (cc, tenant))

    def test_no_phantom_on_clean_slice(self):
        # Precision under no signal: remove the faulty cohorts and assert the engine
        # invents no regression on what remains.
        base = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        drop = set()
        for f in self._real_findings(base).values():
            drop |= _cohort_session_ids(self.sessions, f)
        s = [r for r in self.sessions if r["session_id"] not in drop]
        ids = {r["session_id"] for r in s}
        tc = [r for r in self.tool_calls if r["session_id"] in ids]
        kb = [r for r in self.kb_lookups if r["session_id"] in ids]
        rep = self._run(s, tc, kb, self.config_rows)
        regressions = [f["id"] for f in rep["findings"] if f.get("is_regression")]
        print("clean slice (faulty cohorts removed): %d regressions %s"
              % (len(regressions), regressions))
        self.assertEqual(regressions, [], "phantom regression on a fault-free slice")

    def test_unexplained_fires_on_synthetic_drop(self):
        # The open-world safety net must actually trigger, not merely exist: inject a
        # novel sustained resolution drop with no kb/tool/cost signature and no config
        # change, and assert it surfaces as 'unexplained' rather than being dropped.
        base = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        real_intents = {(f["tenant"], f["cohort"].get("intent"))
                        for f in self._real_findings(base).values()}
        counts = Counter((s["tenant"], s["intent"]) for s in self.sessions)
        cand = None
        for (t, i), n in counts.most_common():
            if (t, i) in real_intents or n < 300:
                continue
            coh = [s for s in self.sessions if s["tenant"] == t and s["intent"] == i]
            res = sum(1 for s in coh if s["session_end"] == "resolved") / len(coh)
            if res >= 0.6:
                cand = (t, i)
                break
        self.assertIsNotNone(cand, "no clean high-resolution cohort available to perturb")
        t, i = cand
        days = sorted(s["day"] for s in self.sessions if s["tenant"] == t and s["intent"] == i)
        cut = days[len(days) // 2]

        def flip(s):
            if (s["tenant"] == t and s["intent"] == i and s["day"] >= cut
                    and s["session_end"] == "resolved"):
                return "handoff"
            return s["session_end"]

        rep = self._run(_rows(self.sessions, session_end=flip),
                        self.tool_calls, self.kb_lookups, self.config_rows)
        fmap = {f["id"]: f for f in rep["findings"]}
        hit = {d["cause_class"] for d in rep["diagnoses"]
               if fmap[d["finding_id"]]["tenant"] == t
               and fmap[d["finding_id"]]["cohort"].get("intent") == i}
        print("synthetic drop on (%s,%s): diagnoses=%s" % (t, i, sorted(hit)))
        self.assertTrue(hit, "a novel sustained drop was silently dropped")
        self.assertIn("unexplained", hit)

    def test_drop_one_fault(self):
        # Count independence: the engine must not assume "exactly three". Remove one
        # real fault's cohort; the other two must still be found and none invented.
        base = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        for cc, f in self._real_findings(base).items():
            drop = _cohort_session_ids(self.sessions, f)
            s = [r for r in self.sessions if r["session_id"] not in drop]
            ids = {r["session_id"] for r in s}
            tc = [r for r in self.tool_calls if r["session_id"] in ids]
            kb = [r for r in self.kb_lookups if r["session_id"] in ids]
            real, _, _ = self._causes(self._run(s, tc, kb, self.config_rows))
            print("dropped %s: remaining real=%s" % (cc, sorted(real & REAL)))
            self.assertNotIn(cc, real, "%s still found after its cohort removed" % cc)
            self.assertTrue((REAL - {cc}) <= real,
                            "dropping %s lost others: %s" % (cc, (REAL - {cc}) - real))

    def test_day_offset_fuzz(self):
        # Beyond the single +90 point: several offsets, incl. non-multiples of 7 that
        # change the day//7 week phase. Nothing may key on an absolute day.
        for off in (1, 3, 7, 13, 90, 200):
            s = _rows(self.sessions, day=lambda r: r["day"] + off)
            tc = _rows(self.tool_calls, day=lambda r: r["day"] + off)
            kb = _rows(self.kb_lookups, day=lambda r: r["day"] + off)
            cfg = _rows(self.config_rows, day=lambda r: r["day"] + off)
            rep = self._run(s, tc, kb, cfg)
            self._assert_all_found(rep, "offset+%d" % off)
            _, _, attr = self._causes(rep)
            for cc, base_day in ATTR_DAY.items():
                self.assertEqual(attr.get(cc), base_day + off,
                                 "offset %d: %s attributed to %s" % (off, cc, attr.get(cc)))

    def test_late_onset_floor(self):
        # A fault planted near the corpus end has fewer post-onset weeks to confirm.
        # Truncate the tail and record how many weeks are needed — documents the floor
        # instead of hiding it.
        onset = ATTR_DAY["tool.contract_break"]
        caught = {}
        for weeks_after in (1, 2, 3):
            end = onset + weeks_after * 7
            s = [r for r in self.sessions if r["day"] <= end]
            ids = {r["session_id"] for r in s}
            tc = [r for r in self.tool_calls if r["session_id"] in ids]
            kb = [r for r in self.kb_lookups if r["session_id"] in ids]
            real, _, _ = self._causes(self._run(s, tc, kb, self.config_rows))
            caught[weeks_after] = "tool.contract_break" in real
            print("truncate to onset+%dw (day %d): tool.contract_break found=%s"
                  % (weeks_after, end, caught[weeks_after]))
        self.assertTrue(caught[3],
                        "tool.contract_break not caught even with 3 post-onset weeks")

    def test_unattributable_config(self):
        # A real regression with no config change nearby must still be reported, with a
        # null attribution and the window falling back to the detected onset (no crash).
        cfg = _rows(self.config_rows, day=lambda r: r["day"] + 1000)
        rep = self._run(self.sessions, self.tool_calls, self.kb_lookups, cfg)
        real, _, _ = self._causes(rep)
        self.assertTrue(REAL <= real, "faults lost when config unattributable: %s"
                        % (REAL - real))
        dmap = {d["finding_id"]: d for d in rep["diagnoses"]}
        for cc, f in self._real_findings(rep).items():
            self.assertIsNone(dmap[f["id"]].get("attributed_change"),
                              "%s attributed to a change 1000 days away" % cc)
            self.assertIsNotNone((f.get("window") or {}).get("from_day"),
                                 "%s lost its window.from_day" % cc)

    def test_deterministic_report(self):
        r1 = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        r2 = self._run(self.sessions, self.tool_calls, self.kb_lookups, self.config_rows)
        for r in (r1, r2):
            r.pop("generated_at", None)  # the only intentionally time-varying field
        self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True),
                         "report is nondeterministic across identical runs")


class PreflightAndDegradation(unittest.TestCase):
    """The crash-proofing half: a malformed sealed corpus must degrade with a reported
    inconsistency, or hard-fail with a clear diagnostic — never a mid-run stack trace."""

    def test_valid_kit_has_no_fatal(self):
        fatal, _ = preflight.validate_kit(KIT, sample=False)
        self.assertEqual(fatal, [], "valid kit flagged fatal: %s" % fatal)

    def test_missing_kit_is_fatal_and_named(self):
        d = tempfile.mkdtemp()
        try:
            fatal, _ = preflight.validate_kit(d)
            self.assertTrue(any("manifest" in f for f in fatal), fatal)
            self.assertTrue(any("sessions" in f for f in fatal), fatal)
        finally:
            shutil.rmtree(d)

    def test_config_timeline_degrades_not_crashes(self):
        d = tempfile.mkdtemp()
        try:
            corpus = os.path.join(d, "corpus")
            os.makedirs(corpus)
            # missing file -> [] (attribution null), never a crash
            self.assertEqual(io_corpus.read_config_timeline(corpus), [])
            # bad day-row skipped; good row kept; missing evidence cols defaulted to ""
            with open(os.path.join(corpus, "config_timeline.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["day", "tenant", "kind", "target"])
                w.writerow(["nope", "acme", "tool", "t1"])   # non-int day -> dropped
                w.writerow(["12", "acme", "tool", "t1"])      # valid
            rows = io_corpus.read_config_timeline(corpus)
            self.assertEqual([r["day"] for r in rows], [12])
            self.assertEqual(rows[0]["note"], "")            # defaulted, no KeyError
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
