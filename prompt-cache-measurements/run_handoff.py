#!/usr/bin/env python3
"""Scenario handoff: two agents (coordinator and report specialist) with handoff edges, created through the API; one conversation, 6 turns,
Anthropic only, thinking on. Turns 2 and 4 are handed over to the specialist, which calls fetch_report. Claude drops the thinking blocks of the other
agent (input_transformations), which is expected and does not change the cache. Summary: analyze_g4.py. Run via run.sh handoff."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402
import run_tools as rt  # noqa: E402

PLAN = [
    ("plain", rt.PLAIN[0] + rt.NO_TOOLS),
    ("handoff", "Hand this over to the report specialist using your transfer tool: " + rt.TOOLS[2]),
    ("plain", rt.PLAIN[1] + rt.NO_TOOLS),
    ("handoff", "Hand this over to the report specialist using your transfer tool: " + rt.TOOLS[4]),
    ("plain", rt.PLAIN[3] + rt.NO_TOOLS),
    ("plain", rt.PLAIN[5] + rt.NO_TOOLS),
]


def make_agents(token, args, label):
    mp = {"model": args.model, "thinking": True, "effort": args.effort, "thinkingDisplay": "omitted", "maxOutputTokens": args.max_tokens,
          "promptCache": True, "promptCacheTtl": args.ttl}
    coord = {"name": f"Coordinator {label}", "provider": "anthropic", "model": args.model, "model_parameters": mp,
             "instructions": f"AGENT-COORD {label}. You are the coordinator. Answer plain questions yourself. For any question about reports, hand off to the "
                             "report specialist with your transfer tool; you cannot fetch reports yourself. " + rt.SYSTEM}
    spec = {"name": f"Specialist {label}", "provider": "anthropic", "model": args.model, "model_parameters": mp, "tools": ["fetch_report_mcp_bigtool"],
            "instructions": f"AGENT-SPEC {label}. You are the report specialist. Fetch the reports with fetch_report and answer. If the user asks for something "
                            "that is not about reports, hand back to the coordinator with your transfer tool. " + rt.SYSTEM}
    ids = []
    for body in (coord, spec):
        status, data = e2e.request("POST", "/api/agents", body, token)
        if status not in (200, 201):
            raise RuntimeError(f"create agent {status}: {data}")
        ids.append(data["id"])
    edges = [{"from": ids[0], "to": ids[1], "edgeType": "handoff", "description": "Hand over report questions to the specialist."},
             {"from": ids[1], "to": ids[0], "edgeType": "handoff", "description": "Hand back to the coordinator."}]
    status, data = e2e.request("PATCH", f"/api/agents/{ids[0]}", {"edges": edges}, token)
    if status != 200:
        raise RuntimeError(f"patch agent {status}: {data}")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["dev", "stack", "replay"])
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--restart-after", type=int, default=0, help="restart LibreChat after this turn (0 = never)")
    ap.add_argument("--ttl", default="5m", choices=["5m", "1h"], help="promptCacheTtl")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    env = e2e.load_env()
    token = e2e.login(env, env["LC_TEST_EMAIL"])
    label = f"{args.arm}-{uuid.uuid4().hex[:8]}"
    ids = make_agents(token, args, label)
    e2e.PROVIDER["endpoint"] = "agents"
    e2e.PROVIDER["preset"] = {"endpoint": "agents", "agent_id": ids[0], "maxContextTokens": 200000}
    e2e.PROVIDER["web"] = False
    e2e.PROVIDER["mcp"] = ["bigtool"]
    conv_id, parent = str(uuid.uuid4()), e2e.NO_PARENT
    run = {"arm": args.arm, "commit": e2e.git_commit(args.arm), "model": args.model, "effort": args.effort, "ttl": args.ttl, "scenario": "handoff",
           "started": e2e.iso_now(), "conversation_id": conv_id, "turns": [], "agents": ids}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(f"{e2e.KIT}/runs-handoff", exist_ok=True)
    out = f"{e2e.KIT}/runs-handoff/{args.arm}-{args.model}{'-' + args.tag if args.tag else ''}-{stamp}.json"
    try:
        for turn, (kind, text) in enumerate(PLAN, 1):
            if args.restart_after and turn == args.restart_after + 1:
                subprocess.run([f"{e2e.KIT}/run.sh", "restart"], check=True)
                token = e2e.login(env, env["LC_TEST_EMAIL"])
            try:
                rec = e2e.send_turn(token, conv_id, parent, f"{text} (ticket {uuid.uuid4().hex[:12]})", False)
            except RuntimeError as err:
                print(f"[{args.arm}] turn {turn} FAILED: {err}", flush=True)
                run["failed_turn"] = turn
                break
            rec.update({"turn": turn, "kind": kind})
            parent = rec["assistant_message_id"]
            run["turns"].append(rec)
            print(f"[{args.arm}] turn {turn} {kind} chars={rec['reply_chars']} err={rec['error']}", flush=True)
    finally:
        run["finished"] = e2e.iso_now()
        json.dump(run, open(out, "w"), indent=1)
    print("run file:", out)
    subprocess.run(["python3", "-I", f"{e2e.KIT}/analyze_g4.py", out, "--out-dir", "results-handoff"], check=False)


if __name__ == "__main__":
    sys.exit(main())
