"""The simulated world: tenants, intents, channels, agents, and the config timeline.

Everything here is deterministic given a seed. No third-party deps — the kit has to
generate on a laptop with a bare Python 3.9.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DAY = 86400
CORPUS_DAYS = 56  # 8 weeks
EPOCH = dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Closed value sets. These are the taxonomy the catalog declares; nothing in the
# corpus may use a value outside them. This is the discipline the real runtime is
# missing in most real deployments.
# --------------------------------------------------------------------------- #

STEP_TYPES = ["turn", "llm_call", "tool_call", "kb_lookup", "routing", "guardrail", "handoff"]

ERROR_CLASSES = [
    "upstream.timeout",
    "upstream.platform_5xx",
    "upstream.client_4xx",
    "upstream.malformed_response",
    "model.content_filter",
    "model.context_overflow",
    "model.provider_error",
    "runtime.max_iterations",
    "runtime.tool_not_found",
    "guardrail.blocked",
]

OUTCOMES = ["ok", "error", "timeout", "max_iterations", "blocked"]
SESSION_ENDS = ["resolved", "handoff", "abandoned"]
CHANNELS = ["web", "whatsapp", "voice", "app"]


@dataclass
class Channel:
    key: str
    weight: float
    latency_mult: float
    turn_mult: float
    abandon_mult: float


CHANNEL_SPECS = {
    "web": Channel("web", 0.34, 1.00, 1.00, 1.00),
    "whatsapp": Channel("whatsapp", 0.38, 1.15, 1.20, 1.35),
    "voice": Channel("voice", 0.16, 0.75, 0.85, 0.60),
    "app": Channel("app", 0.12, 0.95, 0.95, 0.90),
}


@dataclass
class Tool:
    key: str
    p_error: float
    latency_p50: int
    latency_p95: int
    error_mix: Dict[str, float]


@dataclass
class Intent:
    key: str
    label: str
    weight: float
    milestones: List[str]
    tools: List[str]
    needs_kb: bool
    p_resolve: float
    turns_mu: float
    turns_sd: float
    p_handoff_by_design: float
    cost_per_turn: float = 0.011


@dataclass
class Tenant:
    key: str
    label: str
    vertical: str
    daily_sessions: int
    v2_share: float  # share of traffic on legacy flows — emits NO tool_call rows
    agents: List[str]
    tools: Dict[str, Tool]
    intents: List[Intent]
    kb_docs: int

    def intent_map(self) -> Dict[str, Intent]:
        return {i.key: i for i in self.intents}


def _tool(key, p_error, p50, p95, mix=None) -> Tool:
    return Tool(
        key,
        p_error,
        p50,
        p95,
        mix
        or {
            "upstream.timeout": 0.30,
            "upstream.platform_5xx": 0.40,
            "upstream.client_4xx": 0.20,
            "upstream.malformed_response": 0.10,
        },
    )


# --------------------------------------------------------------------------- #
# Tenant A — retail bank. Carries the KB-gap fault and the prompt regression,
# plus the traffic-mix decoy and the v2 coverage hole.
# --------------------------------------------------------------------------- #

ACME = Tenant(
    key="acme-bank",
    label="Acme Bank",
    vertical="financial_services",
    daily_sessions=520,
    v2_share=0.28,
    agents=["acme_main_v3", "acme_cards_v3", "acme_lending_v3", "acme_legacy_v2"],
    kb_docs=418,
    tools={
        "get_balance": _tool("get_balance", 0.011, 210, 690),
        "block_card": _tool("block_card", 0.019, 340, 1150),
        "raise_dispute": _tool("raise_dispute", 0.034, 480, 1720),
        "get_loan_status": _tool("get_loan_status", 0.022, 390, 1310),
        "send_statement": _tool("send_statement", 0.015, 300, 980),
        "get_employment_letter": _tool(
            "get_employment_letter",
            0.061,
            720,
            3400,
            {
                "upstream.timeout": 0.52,
                "upstream.platform_5xx": 0.31,
                "upstream.client_4xx": 0.09,
                "upstream.malformed_response": 0.08,
            },
        ),
        "branch_lookup": _tool("branch_lookup", 0.006, 120, 340),
        "get_fx_rate": _tool("get_fx_rate", 0.009, 160, 420),
    },
    intents=[
        Intent("balance_enquiry", "Balance enquiry", 0.20,
               ["intent_understood", "authenticated", "balance_returned"],
               ["get_balance"], False, 0.94, 3.2, 1.0, 0.02),
        Intent("card_block", "Block a card", 0.11,
               ["intent_understood", "authenticated", "card_identified", "card_blocked"],
               ["block_card"], False, 0.89, 5.1, 1.6, 0.06),
        Intent("txn_dispute", "Dispute a transaction", 0.09,
               ["intent_understood", "authenticated", "txn_identified", "dispute_raised"],
               ["raise_dispute"], True, 0.71, 7.8, 2.4, 0.21),
        Intent("loan_status", "Loan application status", 0.10,
               ["intent_understood", "authenticated", "loan_found", "status_returned"],
               ["get_loan_status"], False, 0.86, 4.4, 1.4, 0.07),
        Intent("statement_request", "Request a statement", 0.09,
               ["intent_understood", "authenticated", "period_chosen", "statement_sent"],
               ["send_statement"], False, 0.91, 4.0, 1.2, 0.04),
        Intent("employment_letter", "Employment letter status", 0.07,
               ["intent_understood", "authenticated", "request_located", "letter_status_returned"],
               ["get_employment_letter"], True, 0.77, 6.2, 2.1, 0.14),
        Intent("branch_locator", "Find a branch or ATM", 0.13,
               ["intent_understood", "location_captured", "branch_returned"],
               ["branch_lookup"], False, 0.97, 2.6, 0.8, 0.01),
        Intent("fx_rate", "Foreign exchange rate", 0.06,
               ["intent_understood", "pair_captured", "rate_returned"],
               ["get_fx_rate"], False, 0.95, 2.4, 0.7, 0.01),
        Intent("product_info", "Product and eligibility questions", 0.15,
               ["intent_understood", "question_classified", "answer_grounded"],
               [], True, 0.82, 4.6, 1.8, 0.11),
    ],
)

# The fault-5 cohort: a product line that launches mid-corpus with no KB behind it.
ACME_PREMIUM_INTENT = Intent(
    "premium_card_info", "Premium card questions", 0.0,
    ["intent_understood", "question_classified", "answer_grounded"],
    [], True, 0.86, 4.5, 1.7, 0.09,
)

# --------------------------------------------------------------------------- #
# Tenant B — e-commerce. Carries the silent tool failure and the volume spike.
# --------------------------------------------------------------------------- #

NORTHWIND = Tenant(
    key="northwind-retail",
    label="Northwind Retail",
    vertical="ecommerce",
    daily_sessions=760,
    v2_share=0.07,
    agents=["nw_care_v3", "nw_orders_v3", "nw_returns_v3"],
    kb_docs=286,
    tools={
        "get_order_status": _tool("get_order_status", 0.014, 240, 810),
        "initiate_return": _tool("initiate_return", 0.026, 520, 1900),
        "get_refund_status": _tool("get_refund_status", 0.021, 380, 1240),
        "check_stock": _tool("check_stock", 0.012, 190, 560),
        "reschedule_delivery": _tool("reschedule_delivery", 0.038, 610, 2300),
        "apply_promo": _tool("apply_promo", 0.031, 280, 940),
        "update_account": _tool("update_account", 0.017, 330, 1020),
    },
    intents=[
        Intent("order_status", "Where is my order", 0.29,
               ["intent_understood", "order_identified", "status_returned"],
               ["get_order_status"], False, 0.92, 3.4, 1.1, 0.03),
        Intent("return_initiate", "Start a return", 0.14,
               ["intent_understood", "order_identified", "item_chosen", "return_created"],
               ["initiate_return"], True, 0.83, 6.0, 2.0, 0.10),
        Intent("refund_status", "Where is my refund", 0.12,
               ["intent_understood", "order_identified", "refund_status_returned"],
               ["get_refund_status"], False, 0.85, 4.2, 1.5, 0.09),
        Intent("product_availability", "Is this in stock", 0.13,
               ["intent_understood", "product_identified", "stock_returned"],
               ["check_stock"], True, 0.90, 3.1, 1.0, 0.04),
        Intent("delivery_reschedule", "Change my delivery", 0.10,
               ["intent_understood", "order_identified", "slot_chosen", "delivery_moved"],
               ["reschedule_delivery"], False, 0.79, 5.6, 1.9, 0.13),
        Intent("promo_apply", "Apply a promo code", 0.08,
               ["intent_understood", "code_captured", "promo_applied"],
               ["apply_promo"], True, 0.74, 4.8, 1.7, 0.16),
        Intent("account_update", "Update my details", 0.07,
               ["intent_understood", "authenticated", "field_updated"],
               ["update_account"], False, 0.88, 4.1, 1.3, 0.05),
        Intent("sizing_help", "Sizing and fit questions", 0.07,
               ["intent_understood", "question_classified", "answer_grounded"],
               [], True, 0.80, 4.4, 1.6, 0.12),
    ],
)

TENANTS = [ACME, NORTHWIND]


# --------------------------------------------------------------------------- #
# Config timeline. Every model / prompt / KB / tool / judge change, timestamped.
# Some of these are the cause of a fault. Most are noise — attribution is only
# interesting because the innocent changes outnumber the guilty ones.
# --------------------------------------------------------------------------- #

@dataclass
class ConfigChange:
    day: int
    tenant: str
    kind: str      # model | prompt | kb | tool | judge | routing
    target: str
    from_value: str
    to_value: str
    note: str


def config_timeline(v: Dict) -> List[ConfigChange]:
    """Innocent changes plus the ones that carry a fault. The sealed corpus moves
    the guilty ones, so a system tuned to the practice dates cannot coast."""
    c: List[ConfigChange] = [
        # ---- innocuous, every corpus --------------------------------------
        ConfigChange(4, "acme-bank", "kb", "kb", "kb_2026_05_a", "kb_2026_06_a", "scheduled KB refresh"),
        ConfigChange(6, "northwind-retail", "routing", "nw_care_v3", "rr_v2", "rr_v3", "routing weights retuned"),
        ConfigChange(9, "acme-bank", "model", "acme_main_v3", "sonnet-4.6", "sonnet-5", "planned model upgrade"),
        ConfigChange(12, "northwind-retail", "prompt", "nw_orders_v3", "p_v7", "p_v8", "tone tweak"),
        ConfigChange(15, "acme-bank", "tool", "branch_lookup", "1.4.0", "1.4.1", "patch release"),
        ConfigChange(18, "northwind-retail", "kb", "kb", "kb_2026_06_a", "kb_2026_06_b", "seasonal content"),
        ConfigChange(23, "acme-bank", "prompt", "acme_cards_v3", "p_v3", "p_v4", "clarified card wording"),
        ConfigChange(26, "northwind-retail", "model", "nw_care_v3", "sonnet-4.6", "sonnet-5", "planned model upgrade"),
        ConfigChange(35, "northwind-retail", "kb", "kb", "kb_2026_06_b", "kb_2026_07_a", "scheduled KB refresh"),
        ConfigChange(41, "acme-bank", "routing", "acme_main_v3", "rr_v4", "rr_v5", "handoff group split"),
        ConfigChange(49, "northwind-retail", "prompt", "nw_returns_v3", "p_v5", "p_v6", "shorter confirmations"),
        ConfigChange(52, "acme-bank", "tool", "get_balance", "2.1.3", "2.1.4", "patch release"),
        # ---- the judge-version boundary (decoy D3) — both tenants ---------
        ConfigChange(v["judge_day"], "*", "judge", "quality_rubric", "v1", "v2",
                     "stricter quality rubric rolled out"),
        # ---- guilty changes ------------------------------------------------
        ConfigChange(v["kb_gap_day"], "acme-bank", "kb", "kb",
                     "kb_2026_06_a", "kb_2026_06_c", "premium card launch content pack"),
        ConfigChange(v["silent_tool_day"], "northwind-retail", "tool", "get_order_status",
                     "3.2.0", "3.3.0", "order service migration"),
        ConfigChange(v["prompt_reg_day"], "acme-bank", "prompt", "acme_main_v3",
                     "p_v3", "p_v4", "safety and confirmation wording"),
    ]
    return sorted(c, key=lambda x: (x.day, x.tenant))


# --------------------------------------------------------------------------- #
# Faults and decoys. `variant` A is the practice corpus handed out on day 0;
# `variant` B is the sealed corpus opened at noon on day 6.
# --------------------------------------------------------------------------- #

FAULT_SCHEDULE = {
    # Windows are laid out so each event has a clean stretch to be detected in.
    # They are NOT all disjoint: the judge boundary deliberately crosses every
    # other window, because that is exactly what makes it dangerous.
    #
    # ONLY the practice schedule lives here. The sealed corpus is derived from a
    # passphrase the organisers hold (sealed_schedule below) — otherwise anyone
    # holding this file could generate the day-6 corpus and its answers.
    "A": {
        "volume_spike_day": 12, "volume_spike_len": 4, "volume_spike_mult": 4.0,
        "mix_shift_day": 22, "mix_shift_len": 10, "mix_shift_mult": 5.0,
        "judge_day": 28,
        "kb_gap_day": 34, "kb_gap_len": 11,
        "silent_tool_day": 40, "silent_tool_len": 12, "silent_tool_rate": 0.14,
        "prompt_reg_day": 46, "prompt_reg_len": 10,
    },
}


def sealed_schedule(secret: str) -> Dict:
    """Derive a fresh fault layout from a passphrase only the organisers know.

    The constraints below are what keep the corpus *teachable* rather than merely
    different: the two acme-bank faults and the acme-bank decoy must not overlap
    each other, or one masks another and a correct answer becomes unfindable.
    Same secret always yields the same corpus, so a sealed run can be reproduced
    after the event.
    """
    rng = random.Random(hashlib.sha256(("nexus-loop/" + secret).encode()).hexdigest())

    def place(spans, lo, hi, gap=2):
        """Lay out (length) spans inside [lo, hi) with a gap, in random order."""
        for _ in range(400):
            starts, cursor, order = {}, lo, list(spans.items())
            rng.shuffle(order)
            slack = (hi - lo) - sum(l for _, l in order) - gap * (len(order) - 1)
            if slack < 0:
                raise ValueError("window too tight")
            for key, length in order:
                cursor += rng.randint(0, max(0, slack // max(1, len(order))))
                starts[key] = cursor
                cursor += length + gap
            if cursor <= hi:
                return starts
        raise ValueError("could not place spans")

    # Each fault is measured against a lookback window. A decoy that lands inside
    # someone's lookback corrupts the BEFORE figure and destroys the signature the
    # fault is meant to teach — a spike sitting in the tool fault's lookback makes
    # its error rate appear to FALL during the fault. So placement is retried until
    # every comparison window is clean.
    LOOKBACK = 13

    def clear(decoy_start, decoy_len, fault_start, fault_len):
        """True when the decoy misses both the fault's lookback and the fault itself."""
        d0, d1 = decoy_start, decoy_start + decoy_len
        f0, f1 = fault_start - LOOKBACK, fault_start + fault_len
        return d1 <= f0 or d0 >= f1

    for attempt in range(500):
        kb_len     = rng.randint(10, 14)
        prompt_len = rng.randint(10, 14)
        mix_len    = rng.randint(9, 11)
        tool_len   = rng.randint(12, 15)
        spike_len  = rng.randint(3, 4)
        try:
            # acme-bank carries F1, F3 and D1 — these three must not overlap.
            acme = place({"kb_gap": kb_len, "prompt_reg": prompt_len, "mix_shift": mix_len},
                         lo=6, hi=CORPUS_DAYS - 1)
            # northwind carries F2 and D2.
            nw = place({"silent_tool": tool_len, "volume_spike": spike_len},
                       lo=4, hi=CORPUS_DAYS - 1)
        except ValueError:
            continue
        if not clear(nw["volume_spike"], spike_len, nw["silent_tool"], tool_len):
            continue
        if not clear(acme["mix_shift"], mix_len, acme["kb_gap"], kb_len):
            continue
        if not clear(acme["mix_shift"], mix_len, acme["prompt_reg"], prompt_len):
            continue
        break
    else:
        raise ValueError("could not find a clean layout for that secret — try another phrase")

    return {
        "volume_spike_day": nw["volume_spike"], "volume_spike_len": spike_len,
        "volume_spike_mult": round(rng.uniform(3.2, 4.2), 2),
        "mix_shift_day": acme["mix_shift"], "mix_shift_len": mix_len,
        "mix_shift_mult": round(rng.uniform(4.2, 5.4), 2),
        "judge_day": rng.randint(20, CORPUS_DAYS - 12),
        "kb_gap_day": acme["kb_gap"], "kb_gap_len": kb_len,
        "silent_tool_day": nw["silent_tool"], "silent_tool_len": tool_len,
        "silent_tool_rate": round(rng.uniform(0.12, 0.20), 3),
        "prompt_reg_day": acme["prompt_reg"], "prompt_reg_len": prompt_len,
    }


def ground_truth(v: Dict, label: str) -> Dict:
    return {
        "variant": label,
        "corpus_days": CORPUS_DAYS,
        "epoch": EPOCH.isoformat(),
        "faults": [
            {
                "id": "F1",
                "kind": "regression",
                "cause_class": "kb.gap",
                "tenant": "acme-bank",
                "cohort": {"intent": "premium_card_info"},
                "onset_day": v["kb_gap_day"],
                "duration_days": v["kb_gap_len"],
                "signature": "new product line launches with no KB content; kb_lookup returns "
                             "no confident hit; answers fall through to a generic reply",
                "primary_effect": "cohort resolution_rate falls to ~0.3 against a tenant standard "
                                  "above 0.8 — exact figures in observed_effects.F1",
                "aggregate_effect": "tenant containment moves ~2pt — invisible without cohorting",
                "attributable_change": {"kind": "kb", "day": v["kb_gap_day"]},
                "accepted_fix_classes": ["kb.add", "kb.synonym"],
                "detection_window_days": [v["kb_gap_day"], v["kb_gap_day"] + 7],
            },
            {
                "id": "F2",
                "kind": "regression",
                "cause_class": "tool.contract_break",
                "tenant": "northwind-retail",
                "cohort": {"intent": "order_status", "tool": "get_order_status"},
                "onset_day": v["silent_tool_day"],
                "duration_days": v["silent_tool_len"],
                "signature": "tool returns HTTP 200 with an empty payload on ~%d%% of calls; "
                             "outcome is recorded 'ok' so every error-rate metric reads flat"
                             % int(v["silent_tool_rate"] * 100),
                "primary_effect": "same-tool retry within session, then unresolved end",
                "aggregate_effect": "tool_error_rate FLAT — the fault is invisible to the "
                                    "obvious metric and only shows in downstream behaviour",
                "attributable_change": {"kind": "tool", "day": v["silent_tool_day"]},
                "accepted_fix_classes": ["tool.validate", "tool.fallback"],
                "detection_window_days": [v["silent_tool_day"], v["silent_tool_day"] + 7],
            },
            {
                "id": "F3",
                "kind": "regression",
                "cause_class": "prompt.regression",
                "tenant": "acme-bank",
                "cohort": {"agent_id": "acme_main_v3"},
                "onset_day": v["prompt_reg_day"],
                "duration_days": v["prompt_reg_len"],
                "signature": "prompt v4 makes the agent over-confirm; turns-to-resolve rises "
                             "~60% while resolution rate stays flat",
                "primary_effect": "median turns ~4 -> ~6 (mean +58%); cost per session roughly "
                                  "+80%; resolution flat — exact figures in observed_effects.F3",
                "aggregate_effect": "resolution FLAT — threshold alerting on outcome metrics "
                                    "misses this entirely; only a standard-relative measure sees it",
                "attributable_change": {"kind": "prompt", "day": v["prompt_reg_day"]},
                "accepted_fix_classes": ["prompt.edit", "revert"],
                "detection_window_days": [v["prompt_reg_day"], v["prompt_reg_day"] + 7],
            },
        ],
        "decoys": [
            {
                "id": "D1",
                "kind": "traffic_mix_shift",
                "tenant": "acme-bank",
                "onset_day": v["mix_shift_day"],
                "duration_days": v["mix_shift_len"],
                "signature": "campaign drives %.1fx branch_locator volume (a trivially contained "
                             "intent); AGGREGATE containment RISES while every cohort is flat or "
                             "slightly worse" % v["mix_shift_mult"],
                "correct_reading": "not a quality event in either direction — stratify by intent",
                "penalty_if_reported": "false_alarm",
            },
            {
                "id": "D2",
                "kind": "load_event",
                "tenant": "northwind-retail",
                "onset_day": v["volume_spike_day"],
                "duration_days": v["volume_spike_len"],
                "signature": "flash sale, %.1fx volume; p95 latency doubles and timeouts rise, "
                             "then self-corrects. Quality is flat throughout"
                             % v["volume_spike_mult"],
                "correct_reading": "a reliability event for the platform view, not a quality "
                                   "regression; it self-corrects inside the sustain window",
                "penalty_if_reported": "false_alarm_if_called_quality_regression",
            },
            {
                "id": "D3",
                "kind": "judge_version_boundary",
                "tenant": "*",
                "onset_day": v["judge_day"],
                "duration_days": CORPUS_DAYS - v["judge_day"],
                "signature": "quality rubric v1 -> v2 is stricter; judged scores drop ~0.6 "
                             "across BOTH tenants at once. judge_version is stamped on every "
                             "judged value, so the information needed to refuse the comparison "
                             "is present in the data",
                "correct_reading": "do not trend judged quality across the boundary; re-score "
                                   "or segment by judge_version",
                "penalty_if_reported": "false_alarm",
            },
        ],
        "coverage_traps": [
            {
                "id": "C1",
                "description": "v2 legacy flow traffic emits no tool_call rows at all.",
                "true_coverage": {
                    "acme-bank": round(1 - ACME.v2_share, 4),
                    "northwind-retail": round(1 - NORTHWIND.v2_share, 4),
                },
                "correct_behaviour": "any tool-derived metric must state coverage and exclude "
                                     "v2 traffic from the denominator, not silently include it",
                "tolerance": 0.03,
            },
            {
                "id": "C2",
                "description": "custom_dims.customer_ref is near-unique. The catalog declares a "
                               "cardinality budget; grouping by it is a planner error.",
                "correct_behaviour": "refuse the breakdown, cite the budget",
            },
        ],
        "unanswerable_asks": [
            {
                "ask_id": "A11",
                "ask": "What is our failover rate — how often did a primary model or tool fail "
                       "and an alternate serve the user instead?",
                "verdict": "NOT_MEASURABLE",
                "why": "no failover mechanism exists, so no failover event is emitted. There is "
                       "no primary/secondary path to observe.",
                "nearest_proxy": "llm_retry events, which are QUALITY retries at a different "
                                 "temperature — they would under-report and read as '0% failover', "
                                 "i.e. as good news",
                "required_event": {
                    "name": "failover",
                    "grain": "step",
                    "fields": ["from_target", "to_target", "reason", "recovered"],
                    "owner": "conversation-runtime",
                },
            }
        ],
        "fidelity_traps": [
            {
                "ask_id": "A09",
                "ask": "How many users gave up out of frustration rather than getting what they "
                       "wanted and leaving?",
                "correct_fidelity": "judged",
                "why": "abandonment is observable; the REASON for it is a judgment about meaning "
                       "and must be a versioned judged signal with a calibration, not inferred "
                       "from the session end code",
            },
            {
                "ask_id": "A04",
                "ask": "How often did a tool call fail?",
                "correct_fidelity": "measured",
                "why": "we executed the call. Answering this with a model is a Rule 1 violation.",
            },
        ],
    }
