#!/usr/bin/env python3
"""Summary of G runs: python3 -I analyze_g.py runs-g/<file>.json ... [--proxy DIR] [--md OUT]
Joins run files with the capture log by conversation id. Main requests have a system prompt;
the summarizer sends none and is counted separately. share = cache read / prompt over main requests;
uncached = prompt - cache read (cache writes count as uncached); miss = a request reads less than 98% of what the
previous main request had cached."""
import json
import os
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 0.02
# USD per million tokens: input, output, cache read, cache write (adjust to the current price list)
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
}


def conversation_rows(recs):
    rows, prev = [], None
    for r in recs:
        u = r.get("usage") or {}
        inp, read, create, out = (u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        row = {"main": bool(r.get("system_head")), "status": r.get("status"), "input": inp, "read": read, "create": create, "output": out,
               "prompt": inp + read + create, "think_hist": r.get("history_thinking_blocks"), "think_new": r.get("thinking_blocks"),
               "transformed": bool(r.get("input_transformations")), "system_same": r.get("system_same"), "tools_same": r.get("tools_same"),
               "first_diff": r.get("first_diff_byte"), "prev_bytes": r.get("prev_body_bytes"), "error": r.get("error")}
        if row["main"] and row["status"] == 200:
            cached_before = (prev["read"] + prev["create"]) if prev else 0
            row["missed"] = max(0, cached_before - read)
            row["miss"] = bool(prev) and row["missed"] > TOLERANCE * max(cached_before, 1)
            prev = row
        rows.append(row)
    return rows


def main():
    args = sys.argv[1:]
    proxy = args[args.index("--proxy") + 1] if "--proxy" in args else f"{KIT}/data/proxy"
    md_out = args[args.index("--md") + 1] if "--md" in args else None
    files = [a for a in args if a.endswith(".json")]
    recs = [json.loads(line) for line in open(f"{proxy}/requests.jsonl") if line.strip()]
    lines = ["| provider | model | arm | scenario | turns | main req | share | misses | uncached | output | other req | thinking in history (max) | errors | cost $ |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    detail = []
    for path in files:
        run = json.load(open(path))
        for conv in run["conversations"]:
            mine = sorted((r for r in recs if r.get("conv") == conv["conversation_id"]), key=lambda r: r["ts"])
            rows = conversation_rows(mine)
            main_ok = [r for r in rows if r["main"] and r["status"] == 200]
            other = [r for r in rows if not r["main"]]
            tot = {k: sum(r[k] for r in main_ok + other) for k in ("input", "read", "create", "output")}
            prompt = sum(r["prompt"] for r in main_ok)
            read = sum(r["read"] for r in main_ok)
            p = PRICES.get(run["model"])
            cost = f"{(tot['input'] * p[0] + tot['output'] * p[1] + tot['read'] * p[2] + tot['create'] * p[3]) / 1e6:.4f}" if p else "-"
            errors = sum(1 for r in rows if r["status"] != 200)
            lines.append(f"| {run['provider']} | {run['model']} | {run['arm']} | {conv['label']} | {len(conv['turns'])} | {len(main_ok)} | "
                         f"{100 * read / max(prompt, 1):.1f}% | {sum(r['miss'] for r in main_ok)} | {prompt - read} | {tot['output']} | {len(other)} | "
                         f"{max((r['think_hist'] or 0) for r in rows) if rows else 0} | {errors} | {cost} |")
            detail.append(f"\n### {run['provider']} {run['arm']} {conv['label']}\n")
            detail.append("| # | main | st | prompt | read | create | out | missed | hist think | new think | transf | sys same | tools same | diff byte/prev |")
            detail.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for i, r in enumerate(rows, 1):
                detail.append(f"| {i} | {'y' if r['main'] else 'n'} | {r['status']} | {r['prompt']} | {r['read']} | {r['create']} | {r['output']} | "
                              f"{r.get('missed', '-')}{'!' if r.get('miss') else ''} | {r['think_hist']} | {r['think_new']} | {'y' if r['transformed'] else '-'} | "
                              f"{r['system_same']} | {r['tools_same']} | {r['first_diff']}/{r['prev_bytes']} |")
                if r["error"]:
                    detail.append(f"error {i}: {r['error'][:300]}")
    text = "\n".join(lines + detail) + "\n"
    if md_out:
        open(md_out, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
