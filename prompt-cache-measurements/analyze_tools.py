#!/usr/bin/env python3
"""Summary of one "tools" scenario run: python3 -I analyze_tools.py runs-tools/<file>.json [--proxy DIR]
Joins the run file with the capture log (data/proxy/requests.jsonl) by conversation id.
Writes results-tools/<run>.json and .md. Success criteria: input_transformations empty on every request, cache_read does not shrink,
cache_creation is about the new tail only."""
import datetime
import json
import os
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 0.02  # share of the previous cached prefix that may be missed before a request counts as a miss


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    path = sys.argv[1]
    proxy = sys.argv[sys.argv.index("--proxy") + 1] if "--proxy" in sys.argv else f"{KIT}/data/proxy"
    run = json.load(open(path))
    conv = run["conversation_id"]
    recs = [json.loads(line) for line in open(f"{proxy}/requests.jsonl") if line.strip()]
    recs = sorted((r for r in recs if r.get("conv") == conv), key=lambda r: r["ts"])
    starts = [(ts(t["t_start"]), t["turn"]) for t in run["turns"]]
    rows = []
    prev = None
    for r in recs:
        t = ts(r["ts"])
        turn = max((n for s, n in starts if s <= t + datetime.timedelta(seconds=1)), default=0)
        u = r.get("usage") or {}
        inp, read, create, out = (u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        prompt = inp + read + create
        tr = r.get("input_transformations")
        row = {
            "n": len(rows) + 1, "turn": turn, "status": r.get("status"), "stop": r.get("stop_reason"), "rid": r["rid"],
            "input": inp, "cache_read": read, "cache_creation": create, "output": out, "prompt": prompt,
            "hit": round(100 * read / prompt, 1) if prompt else 0,
            "transformations": tr, "transformed": bool(tr),
            "history_thinking": r.get("history_thinking_blocks"), "new_thinking": r.get("signed_thinking_blocks"),
            "messages": r.get("messages_n"), "first_diff_byte": r.get("first_diff_byte"), "prev_bytes": r.get("prev_body_bytes"),
            "common_messages": r.get("common_messages"), "system_sha": r.get("system_sha"), "tools_sha": r.get("tools_sha"),
            "system_same": r.get("system_same"), "tools_same": r.get("tools_same"), "beta": r.get("beta"), "error": r.get("error"),
        }
        if prev:
            prev_cached = prev["cache_read"] + prev["cache_creation"]
            row["tail"] = prompt - prev_cached
            row["missed"] = max(0, prev_cached - read)
            row["miss"] = row["missed"] > TOLERANCE * max(prev_cached, 1)
        else:
            row["tail"], row["missed"], row["miss"] = prompt, 0, False
        rows.append(row)
        prev = row
    ok = [r for r in rows if r["status"] == 200]
    tot = {k: sum(r[k] for r in ok) for k in ("input", "cache_read", "cache_creation", "output", "prompt")}
    summary = {
        "arm": run["arm"], "commit": run["commit"], "model": run["model"], "requests": len(rows), "ok": len(ok),
        **tot, "hit_share": round(100 * tot["cache_read"] / max(tot["prompt"], 1), 1),
        "misses": sum(r["miss"] for r in rows), "transformed_requests": sum(r["transformed"] for r in rows),
        "turns_done": len(run["turns"]), "failed_turn": run.get("failed_turn"),
    }
    os.makedirs(f"{KIT}/results-tools", exist_ok=True)
    base = f"{KIT}/results-tools/{os.path.basename(path)[:-5]}"
    json.dump({"summary": summary, "requests": rows}, open(base + ".json", "w"), indent=1)
    lines = [f"# {run['arm']} {run['model']} (commit {run['commit']})", "", json.dumps(summary), "",
             "| # | turn | st | stop | prompt | input | read | create | out | hit% | tail | missed | hist think | new think | transf | diff byte/prev | common msgs/total | sys | tools |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['n']} | {r['turn']} | {r['status']} | {r['stop']} | {r['prompt']} | {r['input']} | {r['cache_read']} | {r['cache_creation']} | {r['output']} | {r['hit']} | {r['tail']} | {r['missed']}{'!' if r['miss'] else ''} | {r['history_thinking']} | {r['new_thinking']} | {json.dumps(r['transformations']) if r['transformed'] else '-'} | {r['first_diff_byte']}/{r['prev_bytes']} | {r['common_messages']}/{r['messages']} | {r['system_sha']}{'' if r['system_same'] in (None, True) else '*'} | {r['tools_sha']}{'' if r['tools_same'] in (None, True) else '*'} |")
        if r["error"]:
            lines.append(f"error {r['n']}: {r['error'][:200]}")
    open(base + ".md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("results:", base + ".md")


if __name__ == "__main__":
    main()
