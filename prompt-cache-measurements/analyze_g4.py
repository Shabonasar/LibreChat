#!/usr/bin/env python3
"""Per-request report of a G4 or handoff run: python3 -I analyze_g4.py runs-g4/<file>.json [--proxy DIR] [--out-dir results-g4]
Joins the run file with the capture log by conversation id. Per request: turn, usage, cache share, miss (reads less than 98% of the previous
cached prefix), thinking blocks in history, dropped blocks (input_transformations), [ref: toolNturnM] labels of tool results in the request,
first differing byte against the previous request. Writes <out-dir>/<run>.md."""
import datetime
import json
import os
import re
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 0.02
REF = re.compile(r"\[ref: (tool\d+turn\d+)\]")


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def ref_labels(proxy, rid):
    try:
        messages = json.load(open(f"{proxy}/bodies/{rid}.json")).get("messages") or []
    except (OSError, ValueError):
        return None
    labels = []
    for m in messages:
        blocks = m["content"] if isinstance(m.get("content"), list) else []
        for b in blocks:
            if b.get("type") == "tool_result":
                found = REF.search(json.dumps(b.get("content"), ensure_ascii=False))
                if found:
                    labels.append(found.group(1))
    return labels


def main():
    path = sys.argv[1]
    proxy = arg("--proxy", f"{KIT}/data/proxy")
    out_dir = os.path.join(KIT, arg("--out-dir", "results-g4"))
    run = json.load(open(path))
    recs = [json.loads(line) for line in open(f"{proxy}/requests.jsonl") if line.strip()]
    recs = sorted((r for r in recs if r.get("conv") == run["conversation_id"] and "rid" in r), key=lambda r: r["ts"])
    starts = [(ts(t["t_start"]), t["turn"]) for t in run["turns"]]
    rows, prev = [], None
    for r in recs:
        t = ts(r["ts"])
        u = r.get("usage") or {}
        inp, read, create, out = (u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        tr = r.get("input_transformations") or []
        row = {"n": len(rows) + 1, "turn": max((n for s, n in starts if s <= t + datetime.timedelta(seconds=1)), default=0), "status": r.get("status"),
               "stop": r.get("stop_reason"), "prompt": inp + read + create, "read": read, "create": create, "output": out,
               "hist_think": r.get("history_thinking_blocks"), "dropped": [f"{x.get('path')}:{x.get('reason')}" for x in tr],
               "labels": ref_labels(proxy, r["rid"]), "first_diff": r.get("first_diff_byte"), "prev_bytes": r.get("prev_body_bytes"), "error": r.get("error")}
        cached_before = (prev["read"] + prev["create"]) if prev else 0
        row["missed"] = max(0, cached_before - read) if prev else 0
        row["miss"] = row["missed"] > TOLERANCE * max(cached_before, 1)
        rows.append(row)
        prev = row
    ok = [r for r in rows if r["status"] == 200]
    prompt, read = sum(r["prompt"] for r in ok), sum(r["read"] for r in ok)
    summary = {"arm": run["arm"], "commit": run["commit"], "model": run["model"], "scenario": run.get("scenario"), "ttl": run.get("ttl"),
               "requests": len(rows), "ok": len(ok), "prompt": prompt, "cache_read": read, "share_pct": round(100 * read / max(prompt, 1), 1),
               "uncached": prompt - read, "misses": sum(r["miss"] for r in rows), "dropped_blocks": sum(len(r["dropped"]) for r in rows),
               "turns_done": len(run["turns"]), "failed_turn": run.get("failed_turn")}
    lines = [f"# {run['arm']} {run['model']} {run.get('scenario')} (commit {run['commit']})", "", json.dumps(summary), "",
             "| # | turn | st | stop | prompt | read | create | out | share% | missed | hist think | dropped | ref labels | first diff/prev bytes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        share = round(100 * r["read"] / r["prompt"], 1) if r["prompt"] else 0
        lines.append(f"| {r['n']} | {r['turn']} | {r['status']} | {r['stop']} | {r['prompt']} | {r['read']} | {r['create']} | {r['output']} | {share} | "
                     f"{r['missed']}{'!' if r['miss'] else ''} | {r['hist_think']} | {','.join(r['dropped']) or '-'} | {','.join(r['labels'] or []) or '-'} | {r['first_diff']}/{r['prev_bytes']} |")
        if r["error"]:
            lines.append(f"error {r['n']}: {str(r['error'])[:200]}")
    os.makedirs(out_dir, exist_ok=True)
    md = os.path.join(out_dir, os.path.basename(path)[:-5] + ".md")
    open(md, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("results:", md)


if __name__ == "__main__":
    main()
