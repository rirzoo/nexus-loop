"""Replay verification — the loop's Verify stage.

This subpackage is the ONLY place in the engine that touches the network, and it lives
under engine/replay/ (not engine/*.py) so the Rule-1 static guard in selfcheck.py keeps
proving the log-analysis core is network-free. Talking to the organisers' deterministic
replay endpoint is not an LLM call and does not use a fact-from-a-model — it checks a
hypothesis we already formed from the logs.
"""
