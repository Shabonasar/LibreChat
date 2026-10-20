#!/usr/bin/env python3
"""Scenario G4 (code interpreter): one conversation, 6 turns, Anthropic only, thinking on. Needs a Code API reachable from the api container
(LIBRECHAT_CODE_BASEURL in .env). Turns: code on an uploaded csv, MCP fetch_report, two chained bash calls via {{toolNturnM}} references,
restart after turn 3, code on a second csv, reference probe, plain. Summary: analyze_g4.py. Run via run.sh g4."""
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

RESTART_AFTER = 3
DATA = f"{e2e.KIT}/data-files"
PLAN = [
    ("code", "Plan first. A csv file readings.csv was uploaded for the code environment. Use the code execution tool to load it with python (pandas or csv) "
             "and compute the mean reading_m3 per status and the sensor_id with the largest reading_m3. Print the results in the tool, then answer in two sentences.",
     [("readings.csv", "execute_code")]),
    ("tools", rt.TOOLS[2], []),
    ("refs", "Plan first. Step 1: run a bash command that prints the squares of 1 to 8, one per line (use seq and awk); make this call alone and wait for its result. "
             "Step 2, in a separate later call: look at the reference label of that tool output (it looks like [ref: toolNturnM]); then run a second bash command that "
             "feeds that output through the placeholder, written exactly as the double-brace placeholder with that key, inside a quoted heredoc, e.g. a command "
             "`cat <<'EOF' | awk '{s+=$1} END{print s}'` followed by a line with the placeholder and a line EOF. Step 3: answer in one sentence with the sum.", []),
    ("code", "Plan first. A second csv file calibration.csv was uploaded for the code environment. Use the code execution tool to load it with python and compute, "
             "per meter_id, limit_m3 minus 10 percent, and the maximum offset_min. Print them, then answer in two sentences.",
     [("calibration.csv", "execute_code")]),
    ("probe", "Step 1: run a bash command that prints the word marker-five; make this call alone and wait for its result before anything else. Step 2, in a separate "
              "later call after you have seen the result: run a bash command that prints, through a quoted heredoc piped to head -c 150, the literal double-brace "
              "placeholder tool0turn0 (written as two opening braces, tool0turn0, two closing braces); that was the reference key of the pumps report from fetch_report "
              "earlier in this conversation. Step 3: answer in one sentence whether the second command printed the pumps report or something else.", []),
    ("plain", rt.PLAIN[3] + rt.NO_TOOLS, []),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["dev", "stack", "replay"])
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--restart-after", type=int, default=RESTART_AFTER)
    ap.add_argument("--ttl", default="5m", choices=["5m", "1h"], help="promptCacheTtl")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    env = e2e.load_env()
    e2e.PROVIDER["endpoint"] = "anthropic"
    e2e.PROVIDER["preset"] = {
        "endpoint": "anthropic", "model": args.model, "modelLabel": "measure", "maxOutputTokens": args.max_tokens, "maxContextTokens": 200000,
        "thinking": True, "effort": args.effort, "thinkingDisplay": "omitted", "promptCache": True, "promptCacheTtl": args.ttl,
        "promptPrefix": f"Run label: {args.arm}-{uuid.uuid4().hex[:8]}. " + rt.SYSTEM,
    }
    e2e.PROVIDER["web"] = False
    e2e.PROVIDER["mcp"] = ["bigtool"]
    token = e2e.login(env, env["LC_TEST_EMAIL"])
    conv_id, parent = str(uuid.uuid4()), e2e.NO_PARENT
    run = {"arm": args.arm, "commit": e2e.git_commit(args.arm), "model": args.model, "effort": args.effort, "ttl": args.ttl, "scenario": "g4",
           "started": e2e.iso_now(), "conversation_id": conv_id, "turns": [], "uploads": [], "restart_after": args.restart_after}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(f"{e2e.KIT}/runs-g4", exist_ok=True)
    out = f"{e2e.KIT}/runs-g4/{args.arm}-{args.model}{'-' + args.tag if args.tag else ''}-{stamp}.json"
    try:
        for turn, (kind, text, attach) in enumerate(PLAN, 1):
            if turn == args.restart_after + 1:
                subprocess.run([f"{e2e.KIT}/run.sh", "restart"], check=True)
                token = e2e.login(env, env["LC_TEST_EMAIL"])
                run["restart_at"] = e2e.iso_now()
            files = []
            for name, tool_resource in attach:
                up = e2e.upload(token, f"{DATA}/{name}", tool_resource, conv_id)
                run["uploads"].append({"turn": turn, "name": name, "tool_resource": tool_resource, "file_id": up.get("file_id"), "source": up.get("source")})
                files.append({k: up[k] for k in ("file_id", "filepath", "type", "filename", "embedded", "source", "height", "width") if up.get(k) is not None})
                print(f"upload {name} -> source={up.get('source')} file_id={up.get('file_id')}", flush=True)
            try:
                rec = e2e.send_turn(token, conv_id, parent, f"{text} (ticket {uuid.uuid4().hex[:12]})", False, files=files, execute_code=True)
            except RuntimeError as err:
                print(f"[{args.arm}] turn {turn} FAILED: {err}", flush=True)
                run["failed_turn"] = turn
                break
            rec.update({"turn": turn, "kind": kind})
            parent = rec["assistant_message_id"]
            run["turns"].append(rec)
            print(f"[{args.arm}] turn {turn} {kind} chars={rec['reply_chars']} err={rec['error']} :: {rec['reply_text'][:160]}", flush=True)
    finally:
        run["finished"] = e2e.iso_now()
        json.dump(run, open(out, "w"), indent=1)
    print("run file:", out)
    subprocess.run(["python3", "-I", f"{e2e.KIT}/analyze_g4.py", out, "--out-dir", "results-g4"], check=False)


if __name__ == "__main__":
    sys.exit(main())
