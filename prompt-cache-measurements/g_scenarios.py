#!/usr/bin/env python3
"""Scenario definitions G1 (plain chat), G1B, G2/G2O1/G2O2 (web search + three MCP servers toggled), G3 (compaction)
and the conversation loop. Used by run_g.py."""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

PREFIX = "You are a concise engineering assistant. Current date and time: {{current_datetime}}."
NO_TOOLS = " Answer directly, do not call any tools."
Q = [p + NO_TOOLS for p in [
    "Write a Python module implementing a token bucket rate limiter with type hints, docstrings and a markdown table of its parameters.",
    "Now add an asyncio variant of the rate limiter with full code and a table comparing both variants.",
    "Write a pytest suite for both variants with a table of test cases.",
    "Write a short design note on burst handling with a table of three burst strategies.",
    "Add Prometheus metrics to the limiter, full code, and a table of the metric names.",
    "Write a CLI wrapper for the limiter with argparse, full code and a table of options.",
    "Explain how to deploy the limiter behind nginx with a table of tuning parameters.",
    "Write a Go port of the limiter with a short table of differences from the Python version.",
    "Summarize the trade-offs of all variants discussed so far in a table.",
    "List five follow-up improvements with a one-line rationale each.",
]]


def fetch(server, topic):
    return f"Call the fetch_report tool of the MCP server {server} for topic '{topic}' and then answer in two sentences: which row has the highest metric value."


def t(prompt, web=False, mcp=(), art=False):
    return {"prompt": prompt, "web": web, "mcp": list(mcp), "artifacts": art}


SC = {
    "G1": [t(q) for q in Q],
    "G1B": [t(Q[5]), t(Q[2]), t(Q[8])],
    "G2": [
        t(Q[0], web=True),
        t(fetch("bigtool", "pumps"), web=True, mcp=["bigtool"]),
        t(fetch("bigtool2", "valves"), web=True, mcp=["bigtool", "bigtool2"]),
        t(fetch("bigtool2", "wells"), web=True, mcp=["bigtool2"]),
        t(Q[3], web=False),
        t(fetch("bigtool3", "sensors"), web=True, mcp=["bigtool3", "bigtool"]),
        t(Q[4], web=True, mcp=["bigtool3", "bigtool"]),
        t(fetch("bigtool", "tanks"), web=True, mcp=["bigtool"]),
    ],
    "G2O1": [t(Q[0], web=True, mcp=["bigtool", "bigtool2", "bigtool3"]), t(Q[1], web=True, mcp=["bigtool", "bigtool2", "bigtool3"])],
    "G2O2": [t(Q[0], web=True, mcp=["bigtool3", "bigtool", "bigtool2"]), t(Q[1], web=True, mcp=["bigtool3", "bigtool", "bigtool2"])],
    "G3": [
        t(Q[0], web=True),
        t(Q[1], web=True),
        t(fetch("bigtool", "pumps"), web=True, mcp=["bigtool"]),
        t(Q[2], web=True, mcp=["bigtool"]),
        t(fetch("bigtool2", "valves"), web=True, mcp=["bigtool", "bigtool2"]),
        t(Q[3], web=True, mcp=["bigtool", "bigtool2"]),
        t(Q[4], web=False, mcp=["bigtool", "bigtool2"]),
        t(fetch("bigtool", "wells"), web=False, mcp=["bigtool2", "bigtool"]),
        t(Q[5], web=False, mcp=["bigtool2"]),
        t(fetch("bigtool2", "tanks"), web=False, mcp=["bigtool2"]),
        t(Q[6], web=False, mcp=["bigtool2"]),
        t(Q[7], web=False, mcp=["bigtool2"]),
    ],
}


def run_conversation(login, label, specs, gap, preset, variant, run):
    conv_id = str(uuid.uuid4())
    parent = e2e.NO_PARENT
    turns = []
    run["conversations"].append({"scenario": label, "label": label, "conversation_id": conv_id, "turns": turns})
    for i, spec in enumerate(specs, 1):
        if i > 1 and gap:
            time.sleep(gap)
        nonce = uuid.uuid4().hex[:12]
        text = f"{spec['prompt']} (ticket {nonce})"
        e2e.PROVIDER["web"] = spec["web"]
        e2e.PROVIDER["mcp"] = spec["mcp"]
        try:
            rec = e2e.send_turn(login(), conv_id, parent, text, spec["artifacts"], preset)
        except RuntimeError as err:
            print(f"[{variant}] {label} turn {i} FAILED: {err}", flush=True)
            break
        rec.update({"turn": i, "nonce": nonce, "artifacts": spec["artifacts"], "web": spec["web"], "mcp": spec["mcp"]})
        parent = rec["assistant_message_id"]
        turns.append(rec)
        print(f"[{variant}] {label} turn {i} web={spec['web']} mcp={spec['mcp']} chars={rec['reply_chars']} err={rec['error']}", flush=True)
    return None
