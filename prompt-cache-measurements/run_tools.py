#!/usr/bin/env python3
"""Scenario "tools": one conversation, 8 turns. The model plans and calls an MCP tool (fetch_report) on turns 2, 4 and 5,
so the cached prefix of later turns contains thinking and tool results; the LibreChat process is restarted after turn 4.
Capture and summary: analyze_tools.py. Run via run.sh tools; direct: python3 -I run_tools.py --arm stack [--model claude-haiku-5-5]"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

TOOL_TURN = 3
RESTART_AFTER = 4
SYSTEM_RULES = " ".join(
    f"Rule {i}: keep answers factual, name the concept first, then the reason, then one concrete example from systems engineering; "
    f"avoid filler, never repeat the question, prefer numbers to adjectives (variant {i % 7})."
    for i in range(1, 111)
)
PURPOSE = (
    "Context: this conversation is an automated compatibility test run by an open-source contributor. It checks that "
    "LibreChat sends the conversation history back to the Claude API unchanged across turns and restarts, so that prompt "
    "caching works; only token counts are read, and the results go into a public LibreChat issue "
    "(LibreChat-AI/LibreChat#17102). Answer each request normally. "
)
SYSTEM = "You are a careful engineering assistant. " + PURPOSE + SYSTEM_RULES
NO_TOOLS = " Final answer in at most five sentences. Do not call any tools."
# neutral process-engineering problems: rate-limiter wording tripped the cyber refusal classifier (stop_details.category "cyber")
PLAIN = [
    "A tank holds 120 m3 and is filled at 7 m3/min. Every 9 minutes, starting at t=0, a batch of 50 m3 is drawn if available, "
    "otherwise the batch is skipped. The tank starts full. How many batches are skipped in the first 60 minutes?",
    "Same tank and schedule, but each batch waits in a buffer vessel of 40 m3 that drains at 7 m3/min. How much volume "
    "overflows in the first 60 minutes, and what is the longest time a cubic metre spends in the buffer?",
    None,
    "Two flow meters on parallel lines integrate over 10-minute windows with a limit of 70 m3, and their clocks differ by 1.5 "
    "minutes; flow alternates between the lines. Construct the worst case and give the maximum volume that can pass in any "
    "real 10-minute interval without either meter exceeding its limit.",
    "Two controllers share one setpoint register: each reads, adds a correction, then writes. Give an interleaving that loses "
    "a correction, and estimate how often it happens at 2000 updates per second with a 0.5 ms read-write gap.",
    "Using your numbers from the earlier answers, rank the control strategies discussed by cost per delivered cubic metre if "
    "a remote register access costs 0.4 ms and a local check 2 microseconds.",
    "Design three alarm rules that would catch each misconfiguration discussed so far, with thresholds derived from the "
    "earlier numbers.",
    "Re-check the arithmetic of your earlier answers; correct any error, then summarize the discussion in two sentences.",
]


# planning wording only: prompts that mention the model's reasoning are refused as reasoning_extraction
THINK_TOOLS = (
    "Plan first: decide which reports you need and in what order, and after each report decide whether the next one is "
    "still needed. "
)
# scenario "tools": the model thinks before and between tool calls, so the cached prefix of a turn contains thinking
TOOLS = {
    2: THINK_TOOLS + "Call fetch_report of the MCP server bigtool for topic 'pumps'. Then call it for topic 'valves'. "
       "Answer in three sentences: compare the rows with the highest metric value in both reports.",
    4: THINK_TOOLS + "Call fetch_report of bigtool for topic 'wells', then for topic 'tanks'. Answer in three sentences: "
       "which of the four reports so far has the largest single metric value, and by how much it exceeds the others.",
    5: THINK_TOOLS + "Call fetch_report of bigtool for topic 'sensors'. Answer in two sentences: does it change your "
       "previous conclusion, and why.",
}
PLAIN_TOOLS = {1: PLAIN[0], 3: PLAIN[1], 6: PLAIN[3], 7: PLAIN[5], 8: PLAIN[7]}


def prompt_for(turn, scenario="base"):
    if scenario == "tools":
        return TOOLS[turn] if turn in TOOLS else PLAIN_TOOLS[turn] + NO_TOOLS
    if turn == TOOL_TURN:
        return "Call the fetch_report tool of the MCP server bigtool for topic 'pumps', then answer in two sentences: which row has the highest metric value."
    return PLAIN[turn - 1] + NO_TOOLS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["dev", "stack", "replay"])
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--display", default="omitted")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--tag", default="")
    ap.add_argument("--ttl", default="5m", choices=["5m", "1h"], help="promptCacheTtl")
    ap.add_argument("--scenario", default="tools", choices=["base", "tools"])
    args = ap.parse_args()
    env = e2e.load_env()
    e2e.PROVIDER["endpoint"] = "anthropic"
    e2e.PROVIDER["preset"] = {
        "endpoint": "anthropic",
        "model": args.model,
        "modelLabel": "measure",
        "maxOutputTokens": args.max_tokens,
        "maxContextTokens": 200000,
        "thinking": True,
        "effort": args.effort,
        "thinkingDisplay": args.display,
        "promptCache": True,
        "promptCacheTtl": args.ttl,
        # per-run label first, so arms never read each other's cache
        "promptPrefix": f"Run label: {args.arm}-{uuid.uuid4().hex[:8]}. " + SYSTEM,
    }
    e2e.PROVIDER["web"] = False
    e2e.PROVIDER["mcp"] = ["bigtool"]
    token = e2e.login(env, env["LC_TEST_EMAIL"])
    conv_id = str(uuid.uuid4())
    parent = e2e.NO_PARENT
    run = {"arm": args.arm, "commit": e2e.git_commit(args.arm), "model": args.model, "effort": args.effort, "display": args.display,
           "max_tokens": args.max_tokens, "ttl": args.ttl, "scenario": args.scenario, "started": e2e.iso_now(), "conversation_id": conv_id, "turns": [], "restart_after": RESTART_AFTER}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(f"{e2e.KIT}/runs-tools", exist_ok=True)
    out = f"{e2e.KIT}/runs-tools/{args.arm}-{args.model}{'-' + args.scenario if args.scenario != 'base' else ''}{'-' + args.tag if args.tag else ''}-{stamp}.json"
    try:
        for turn in range(1, args.turns + 1):
            if turn == RESTART_AFTER + 1:
                subprocess.run([f"{e2e.KIT}/run.sh", "restart"], check=True)
                token = e2e.login(env, env["LC_TEST_EMAIL"])
                run["restart_at"] = e2e.iso_now()
            nonce = uuid.uuid4().hex[:12]
            text = f"{prompt_for(turn, args.scenario)} (ticket {nonce})"
            try:
                rec = e2e.send_turn(token, conv_id, parent, text, False)
            except RuntimeError as err:
                print(f"[{args.arm}] turn {turn} FAILED: {err}", flush=True)
                run["failed_turn"] = turn
                break
            rec.update({"turn": turn, "nonce": nonce, "tool_turn": turn in TOOLS if args.scenario == "tools" else turn == TOOL_TURN})
            parent = rec["assistant_message_id"]
            run["turns"].append(rec)
            print(f"[{args.arm}] turn {turn} chars={rec['reply_chars']} err={rec['error']}", flush=True)
    finally:
        run["finished"] = e2e.iso_now()
        json.dump(run, open(out, "w"), indent=1)
    print("run file:", out)
    subprocess.run(["python3", "-I", f"{e2e.KIT}/analyze_tools.py", out], check=False)


if __name__ == "__main__":
    sys.exit(main())
