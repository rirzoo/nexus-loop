"""Sound joins against config_timeline.

Two SOUND_WITH_CARE traps from catalog.json are handled in one place:
  - config_change.tenant may be '*' (applies to all tenants). An equality join on
    tenant alone silently drops the judge-version change. config_changes_for treats
    '*' as matching every tenant.
  - step.tool_name -> config_change.target is valid only for kind='tool'; `target`
    also holds agent_ids for prompt/model/routing rows. tool_config_changes filters
    kind before matching target.
"""
from __future__ import annotations


def config_changes_for(config_rows, tenant=None, kind=None, target=None,
                        day_window=None):
    """Filter config rows. tenant matches the given tenant OR a '*' wildcard row.
    day_window is an inclusive (lo, hi) day range."""
    out = []
    for r in config_rows:
        if tenant is not None and r["tenant"] != tenant and r["tenant"] != "*":
            continue
        if kind is not None and r["kind"] != kind:
            continue
        if target is not None and r["target"] != target:
            continue
        if day_window is not None and not (day_window[0] <= r["day"] <= day_window[1]):
            continue
        out.append(r)
    return out


def tool_config_changes(config_rows, tool_name=None):
    """Config rows for tool changes only (kind='tool'), optionally one tool."""
    return [r for r in config_rows
            if r["kind"] == "tool" and (tool_name is None or r["target"] == tool_name)]


def judge_change(config_rows):
    """The single judge-version boundary row (kind='judge', tenant='*'), or None."""
    hits = [r for r in config_rows if r["kind"] == "judge"]
    return hits[0] if hits else None
