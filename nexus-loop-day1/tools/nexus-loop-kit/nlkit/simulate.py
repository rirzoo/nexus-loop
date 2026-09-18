"""Session simulator.

Produces three streams: sessions, steps (the fact grain), and turns (text).
Faults and decoys are applied as parameter shifts on matching sessions, so they
are discoverable by legitimate analysis rather than by pattern-matching a flag.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import zlib
from typing import Dict, Iterable, List, Optional, Tuple

from . import world as W
from .world import CORPUS_DAYS, EPOCH, FAULT_SCHEDULE, ConfigChange, Intent, Tenant

# hour-of-day weights, roughly a support-desk curve
HOUR_CURVE = [.012, .008, .006, .005, .006, .011, .022, .041, .062, .078, .085, .083,
              .076, .079, .082, .078, .068, .055, .043, .035, .029, .024, .019, .015]
WEEKDAY_FACTOR = [1.06, 1.09, 1.07, 1.04, 0.98, 0.62, 0.51]  # Mon..Sun

GUARDRAIL_RATE = 0.011
ABANDON_BASE = 0.055


def _pick(rng: random.Random, weights: Dict[str, float]) -> str:
    total = sum(weights.values())
    r = rng.random() * total
    acc = 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(iter(weights))


def _lognormalish(rng: random.Random, mu: float, sd: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(rng.gauss(mu, sd)))))


def _latency(rng: random.Random, p50: int, p95: int, mult: float = 1.0) -> int:
    # lognormal fitted through the two quantiles
    sigma = math.log(max(p95, p50 + 1) / max(p50, 1)) / 1.645
    val = math.exp(rng.gauss(math.log(max(p50, 1)), sigma))
    return int(max(8, val * mult))


class ConfigState:
    """Resolves the effective config for (tenant, target) at a given day."""

    def __init__(self, changes: List[ConfigChange]):
        self.changes = changes

    def value(self, tenant: str, kind: str, target: str, day: int, default: str) -> str:
        cur = default
        for c in self.changes:
            if c.day > day:
                break
            if c.kind != kind:
                continue
            if c.tenant not in ("*", tenant):
                continue
            if c.target not in ("*", target, "kb", "quality_rubric"):
                continue
            if kind in ("kb", "judge") or c.target == target:
                cur = c.to_value
        return cur


class Simulator:
    def __init__(self, schedule, seed: int, scale: float = 1.0, label: str = "A"):
        # `schedule` is a resolved fault layout: W.FAULT_SCHEDULE["A"] for the
        # practice corpus, or W.sealed_schedule(secret) for the sealed one.
        if isinstance(schedule, str):                     # convenience for tests
            schedule, label = W.FAULT_SCHEDULE[schedule], schedule
        self.label = label
        self.v = schedule
        self.scale = scale
        # zlib.crc32, NOT hash(): str hashing is salted per process in Python 3,
        # so hash() here made the corpus unreproducible between runs.
        self.rng = random.Random(seed * 7919 + zlib.crc32(label.encode()))
        self.changes = W.config_timeline(schedule)
        self.cfg = ConfigState(self.changes)
        self._sid = 0

    # ---------------- volume ------------------------------------------------
    def sessions_for_day(self, t: Tenant, day: int) -> int:
        base = t.daily_sessions * self.scale
        d = (EPOCH + dt.timedelta(days=day)).weekday()
        n = base * WEEKDAY_FACTOR[d]
        n *= 1.0 + 0.004 * day                     # slow organic growth
        n *= 1.0 + 0.06 * math.sin(day / 9.0)      # gentle wobble
        v = self.v
        if t.key == "northwind-retail" and v["volume_spike_day"] <= day < v["volume_spike_day"] + v["volume_spike_len"]:
            n *= v["volume_spike_mult"]            # D2
        return max(1, int(self.rng.gauss(n, n * 0.06)))

    def intent_weights(self, t: Tenant, day: int) -> Dict[str, float]:
        w = {i.key: i.weight for i in t.intents}
        v = self.v
        if t.key == "acme-bank":
            if v["mix_shift_day"] <= day < v["mix_shift_day"] + v["mix_shift_len"]:
                w["branch_locator"] *= v["mix_shift_mult"]          # D1
            if day >= v["kb_gap_day"]:
                ramp = min(1.0, (day - v["kb_gap_day"]) / 5.0)
                w["premium_card_info"] = 0.055 * ramp               # F1 cohort appears
        return w

    # ---------------- flags -------------------------------------------------
    def in_window(self, day: int, start_key: str, len_key: str) -> bool:
        return self.v[start_key] <= day < self.v[start_key] + self.v[len_key]

    # ---------------- one session ------------------------------------------
    def session(self, t: Tenant, day: int, imap: Dict[str, Intent]) -> Tuple[dict, List[dict], List[dict]]:
        rng = self.rng
        v = self.v
        self._sid += 1
        sid = "s_%s_%06d" % (t.key.split("-")[0][:4], self._sid)

        hour = _pick(rng, {str(h): HOUR_CURVE[h] for h in range(24)})
        ts0 = EPOCH + dt.timedelta(days=day, hours=int(hour),
                                   minutes=rng.randrange(60), seconds=rng.randrange(60))

        intent_key = _pick(rng, self.intent_weights(t, day))
        intent = imap[intent_key]
        ch_key = _pick(rng, {c.key: c.weight for c in W.CHANNEL_SPECS.values()})
        ch = W.CHANNEL_SPECS[ch_key]

        is_v2 = rng.random() < t.v2_share
        agent_id = "%s_legacy_v2" % t.key.split("-")[0] if is_v2 else rng.choice(
            [a for a in t.agents if a.endswith("v3")])
        agent_kind = "v2_flow" if is_v2 else "v3_agent"

        model = self.cfg.value(t.key, "model", agent_id, day, "sonnet-4.6")
        prompt_v = self.cfg.value(t.key, "prompt", agent_id, day, "p_v3")
        kb_v = self.cfg.value(t.key, "kb", "kb", day, "kb_2026_05_a")
        judge_v = self.cfg.value(t.key, "judge", "quality_rubric", day, "v1")

        # ---- fault flags for this session ---------------------------------
        f_kb_gap = (t.key == "acme-bank" and intent_key == "premium_card_info"
                    and self.in_window(day, "kb_gap_day", "kb_gap_len"))
        f_prompt = (t.key == "acme-bank" and agent_id == "acme_main_v3"
                    and self.in_window(day, "prompt_reg_day", "prompt_reg_len"))
        f_tool_win = (t.key == "northwind-retail" and intent_key == "order_status"
                      and self.in_window(day, "silent_tool_day", "silent_tool_len"))
        d_load = (t.key == "northwind-retail"
                  and self.in_window(day, "volume_spike_day", "volume_spike_len"))

        # ---- shape of the session -----------------------------------------
        p_resolve = intent.p_resolve
        turns_mu = intent.turns_mu * ch.turn_mult
        if f_kb_gap:
            turns_mu *= 1.25
        if f_prompt:
            turns_mu *= 1.58            # the whole signature: turns up, outcome flat
        if is_v2:
            p_resolve *= 0.94
            turns_mu *= 1.08
        lat_mult = ch.latency_mult * (2.2 if d_load else 1.0)

        n_turns = _lognormalish(rng, turns_mu, intent.turns_sd, 1, 26)
        steps: List[dict] = []
        turns: List[dict] = []
        cursor = ts0
        cost = 0.0
        tool_errors = 0
        silent_fail = False
        retried_tool = False
        guardrail_hits = 0
        kb_miss = 0
        milestones: List[str] = []
        seq = 0

        def add(step_type: str, **kw) -> None:
            nonlocal seq
            seq += 1
            row = {
                "session_id": sid, "tenant": t.key, "step_seq": seq,
                "ts": cursor.isoformat(), "day": day, "step_type": step_type,
                "agent_id": agent_id, "agent_kind": agent_kind, "channel": ch_key,
                "intent": intent_key, "model": model, "prompt_version": prompt_v,
                "kb_version": kb_v,
            }
            row.update(kw)
            steps.append(row)

        action_turn = max(1, min(n_turns, int(round(n_turns * 0.62))))
        reach = intent.milestones[:]
        n_ms = len(reach)

        for turn_i in range(1, n_turns + 1):
            # --- the turn envelope ---
            t_lat = _latency(rng, 900, 3200, lat_mult)
            ms = None
            if turn_i == 1:
                ms = reach[0]
                milestones.append(ms)
            add("turn", turn_index=turn_i, outcome="ok", duration_ms=t_lat,
                error_class=None, milestone=ms)

            turns.append({
                "session_id": sid, "tenant": t.key, "turn_index": turn_i,
                "ts": cursor.isoformat(),
                "user_text": _user_text(rng, intent, turn_i),
                "bot_text": _bot_text(rng, intent, turn_i, f_kb_gap, silent_fail, f_prompt),
            })

            # --- llm call(s) ---
            n_llm = 1 + (1 if (f_prompt and rng.random() < 0.18) else 0)
            for _ in range(n_llm):
                in_tok = rng.randrange(700, 3400)
                out_tok = rng.randrange(60, 420)
                c = (in_tok * 3e-6) + (out_tok * 1.5e-5)
                cost += c
                bad = rng.random() < (0.004 if not d_load else 0.014)
                add("llm_call", turn_index=turn_i,
                    outcome="error" if bad else "ok",
                    error_class=("model.provider_error" if bad else None),
                    duration_ms=_latency(rng, 1100, 4200, lat_mult),
                    input_tokens=in_tok, output_tokens=out_tok, cost_usd=round(c, 6),
                    retry_count=0)
                if bad:
                    add("llm_call", turn_index=turn_i, outcome="ok", error_class=None,
                        duration_ms=_latency(rng, 1100, 4200, lat_mult),
                        input_tokens=in_tok, output_tokens=out_tok,
                        cost_usd=round(c, 6), retry_count=1, retry_reason="provider_error")

            # --- kb lookup ---
            if intent.needs_kb and turn_i <= max(2, action_turn) and rng.random() < 0.72:
                if f_kb_gap:
                    score = round(rng.uniform(0.11, 0.38), 3)
                    hit = False
                    kb_miss += 1
                else:
                    hit = rng.random() < 0.91
                    score = round(rng.uniform(0.62, 0.96) if hit else rng.uniform(0.18, 0.49), 3)
                    if not hit:
                        kb_miss += 1
                add("kb_lookup", turn_index=turn_i, outcome="ok", error_class=None,
                    duration_ms=_latency(rng, 90, 320, lat_mult),
                    kb_hit=hit, kb_top_score=score, kb_docs_considered=rng.randrange(3, 12))

            # --- tool call ---
            if intent.tools and not is_v2 and turn_i in (action_turn, action_turn + 1):
                if turn_i == action_turn or retried_tool:
                    tool_key = intent.tools[0]
                    tool = t.tools[tool_key]
                    tool_v = self.cfg.value(t.key, "tool", tool_key, day, "1.0.0")
                    p_err = tool.p_error * (2.4 if d_load else 1.0)
                    errored = rng.random() < p_err
                    silent = (f_tool_win and tool_key == "get_order_status"
                              and not errored and rng.random() < v["silent_tool_rate"])
                    if errored:
                        tool_errors += 1
                        ec = _pick(rng, tool.error_mix)
                        sc = {"upstream.timeout": 0, "upstream.platform_5xx": 503,
                              "upstream.client_4xx": 422,
                              "upstream.malformed_response": 200}[ec]
                        add("tool_call", turn_index=turn_i, tool_name=tool_key,
                            tool_version=tool_v,
                            outcome="timeout" if ec == "upstream.timeout" else "error",
                            error_class=ec, status_code=sc,
                            duration_ms=_latency(rng, tool.latency_p50, tool.latency_p95,
                                                 lat_mult * (3.0 if ec == "upstream.timeout" else 1.0)),
                            response_bytes=0, result_field_count=0, retry_count=0)
                    else:
                        # THE SILENT FAILURE: 200, outcome ok, empty payload.
                        add("tool_call", turn_index=turn_i, tool_name=tool_key,
                            tool_version=tool_v, outcome="ok", error_class=None,
                            status_code=200,
                            duration_ms=_latency(rng, tool.latency_p50, tool.latency_p95, lat_mult),
                            response_bytes=2 if silent else rng.randrange(220, 4800),
                            result_field_count=0 if silent else rng.randrange(3, 18),
                            retry_count=1 if retried_tool else 0)
                        if silent:
                            silent_fail = True
                    if silent_fail and not retried_tool and turn_i == action_turn:
                        retried_tool = True

            # --- guardrail ---
            if rng.random() < GUARDRAIL_RATE:
                guardrail_hits += 1
                add("guardrail", turn_index=turn_i, outcome="blocked",
                    error_class="guardrail.blocked", duration_ms=rng.randrange(4, 30),
                    guardrail_name=rng.choice(["pii_redaction", "toxicity", "off_topic",
                                               "competitor_mention"]))

            # --- progress a milestone ---
            frac = turn_i / float(n_turns)
            want = 1 + int(frac * (n_ms - 1))
            while len(milestones) < min(want, n_ms - 1):
                milestones.append(reach[len(milestones)])

            cursor += dt.timedelta(milliseconds=t_lat + rng.randrange(1500, 42000))

        # ---- how it ended --------------------------------------------------
        if silent_fail:
            p_resolve *= 0.22
        if tool_errors:
            p_resolve *= 0.35
        if kb_miss >= 2:
            p_resolve *= 0.55
        if f_kb_gap:
            p_resolve = 0.34            # an override, not another multiplier
        # F3 is deliberately outcome-neutral: nothing here may touch how it ENDS.
        p_abandon = ABANDON_BASE * ch.abandon_mult

        r = rng.random()
        if r < p_abandon:
            end = "abandoned"
        elif rng.random() < p_resolve:
            end = "resolved"
        else:
            end = "handoff"

        by_design = None
        if end == "handoff":
            by_design = rng.random() < intent.p_handoff_by_design / max(
                intent.p_handoff_by_design + (1 - intent.p_resolve), 1e-6)
            if silent_fail or tool_errors or f_kb_gap:
                by_design = False
            add("handoff", turn_index=n_turns, outcome="ok", error_class=None,
                duration_ms=rng.randrange(200, 2500),
                handoff_by_design=bool(by_design),
                handoff_group=rng.choice(["tier1", "tier2", "specialist"]))
        if end == "resolved" and len(milestones) < n_ms:
            milestones.append(reach[-1])

        # ---- judged fields (the D3 boundary lives here) ---------------------
        latent = 3.1 + (1.05 if end == "resolved" else 0.0) - (0.75 if end == "abandoned" else 0.0)
        latent -= 0.55 * min(tool_errors, 2) + (0.9 if silent_fail else 0.0)
        latent -= 0.7 if f_kb_gap else 0.0
        latent -= 0.25 if f_prompt else 0.0
        latent += rng.gauss(0, 0.55)
        if judge_v == "v2":
            latent -= 0.60                                # D3: the rubric got stricter
        quality = max(1.0, min(5.0, round(latent, 2)))

        csat = None
        if rng.random() < 0.086:                          # sparse, like reality
            csat = max(1, min(5, int(round(quality + rng.gauss(0, 0.8)))))

        duration_s = int((cursor - ts0).total_seconds())
        sess = {
            "session_id": sid, "tenant": t.key, "day": day, "ts_start": ts0.isoformat(),
            "ts_end": cursor.isoformat(), "channel": ch_key, "intent": intent_key,
            "agent_id": agent_id, "agent_kind": agent_kind, "model": model,
            "prompt_version": prompt_v, "kb_version": kb_v,
            "turns": n_turns, "duration_s": duration_s,
            "session_end": end, "handoff_by_design": by_design,
            "tool_error_count": tool_errors, "kb_miss_count": kb_miss,
            "guardrail_blocks": guardrail_hits,
            "cost_usd": round(cost, 6),
            "milestones_reached": milestones,
            "milestones_total": n_ms,
            "quality_score": quality, "judge_version": judge_v,
            "csat": csat,
            "custom_dims": {"customer_ref": "c_%08x" % rng.getrandbits(32),
                            "segment": rng.choice(["mass", "affluent", "priority"])},
        }
        return sess, steps, turns

    # ---------------- the whole corpus -------------------------------------
    def run(self) -> Iterable[Tuple[dict, List[dict], List[dict]]]:
        for t in W.TENANTS:
            imap = t.intent_map()
            if t.key == "acme-bank":
                imap["premium_card_info"] = W.ACME_PREMIUM_INTENT
            for day in range(CORPUS_DAYS):
                for _ in range(self.sessions_for_day(t, day)):
                    yield self.session(t, day, imap)


# --------------------------------------------------------------------------- #
# Transcript text. Deliberately thin and repetitive — real transcripts do not
# say why something failed, which is the point (a 500, a timeout, a guardrail
# block and "didn't know" all render as the same apology).
# --------------------------------------------------------------------------- #

_OPENERS = {
    "balance_enquiry": ["what's my balance", "how much do i have in savings", "check balance please"],
    "card_block": ["i lost my card", "block my debit card now", "someone used my card"],
    "txn_dispute": ["there's a charge i didn't make", "i want to dispute a transaction"],
    "loan_status": ["where is my loan application", "loan status please"],
    "statement_request": ["send me last month's statement", "i need a statement pdf"],
    "employment_letter": ["status of my employment letter", "did my employment letter get issued"],
    "branch_locator": ["nearest branch", "atm near me", "which branch is open now"],
    "fx_rate": ["usd to inr rate", "what's the fx rate today"],
    "product_info": ["what are the charges on the gold account", "am i eligible for a credit card"],
    "premium_card_info": ["tell me about the premium card", "what are the premium card benefits",
                          "is the premium card worth the fee"],
    "order_status": ["where is my order", "order hasn't arrived", "track my parcel"],
    "return_initiate": ["i want to return this", "start a return please"],
    "refund_status": ["where's my refund", "refund not received"],
    "product_availability": ["is this in stock", "do you have it in medium"],
    "delivery_reschedule": ["change my delivery date", "can you deliver saturday"],
    "promo_apply": ["my promo code isn't working", "apply discount code"],
    "account_update": ["change my phone number", "update my address"],
    "sizing_help": ["what size should i get", "does it run small"],
}
_FOLLOWUPS = ["yes", "ok", "that's right", "no that's not it", "still waiting", "can you check again",
              "hmm", "and what about the other one", "thanks", "sure", "correct"]
_APOLOGY = "Sorry, I'm having trouble right now. Please try again."
_GENERIC = "I can help with that. Could you confirm a few details?"
_CONFIRM = "Just to confirm before I proceed — is that correct?"


def _user_text(rng: random.Random, intent: Intent, turn_i: int) -> str:
    if turn_i == 1:
        return rng.choice(_OPENERS.get(intent.key, ["hi"]))
    return rng.choice(_FOLLOWUPS)


def _bot_text(rng: random.Random, intent: Intent, turn_i: int,
              kb_gap: bool, silent: bool, over_confirm: bool) -> str:
    if silent and rng.random() < 0.5:
        return _APOLOGY
    if kb_gap and rng.random() < 0.6:
        return ("I don't have specific information on that. I can connect you with "
                "someone who does.")
    if over_confirm and rng.random() < 0.5:
        return _CONFIRM
    return _GENERIC
