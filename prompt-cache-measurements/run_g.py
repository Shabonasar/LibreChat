#!/usr/bin/env python3
"""G1-G3 against a real provider API (the capture proxy logs the requests). Scenarios and prompts: g_scenarios.py.
Run via run.sh g; direct: python3 -I run_g.py --arm stack --provider anthropic|openai|kimi|zai --scenarios G1,G1B [--gap 65]"""
import argparse
import datetime
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402
import g_scenarios as eg  # noqa: E402

PURPOSE = (
    " Context: this conversation is an automated compatibility test run by an open-source contributor. It checks that "
    "LibreChat keeps the request prefix stable across turns so provider prompt caching works; the results go into a public "
    "LibreChat issue (LibreChat-AI/LibreChat#17102). Only token counts are read; answer each request normally."
)


def presets(model, effort, max_tokens, ttl="5m"):
    custom = {"endpointType": "custom", "max_tokens": max_tokens, "maxContextTokens": 128000}
    return {
        "anthropic": ("anthropic", {"endpoint": "anthropic", "model": model or "claude-haiku-5-5", "maxOutputTokens": max_tokens,
                                    "maxContextTokens": 128000, "thinking": True, "effort": effort, "thinkingDisplay": "omitted",
                                    "promptCache": True, "promptCacheTtl": ttl}),
        "openai": ("OpenAI", {**custom, "endpoint": "OpenAI", "model": model or "gpt-5.4-nano", "useResponsesApi": True,
                              "reasoning_effort": effort}),
        "kimi": ("Kimi", {**custom, "endpoint": "Kimi", "model": model or "kimi-for-coding"}),
        "zai": ("Z.ai", {**custom, "endpoint": "Z.ai", "model": model or "glm-5.2"}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["dev", "stack", "replay"])
    ap.add_argument("--provider", required=True, choices=["anthropic", "openai", "kimi", "zai"])
    ap.add_argument("--model", default="")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=int(os.environ.get("G_MAX_TOKENS", "4096")))
    ap.add_argument("--scenarios", default="G1")
    ap.add_argument("--gap", type=int, default=0, help="seconds between turns")
    ap.add_argument("--max-context", type=int, default=0, help="maxContextTokens (G3: 12000 or 32000)")
    ap.add_argument("--label", default="")
    ap.add_argument("--ttl", default="5m", choices=["5m", "1h"], help="promptCacheTtl (anthropic only)")
    args = ap.parse_args()
    endpoint, preset = presets(args.model, args.effort, args.max_tokens, args.ttl)[args.provider]
    e2e.PROVIDER["endpoint"] = endpoint
    e2e.PROVIDER["preset"] = preset
    env = e2e.load_env()
    cached = {"token": e2e.login(env, env["LC_TEST_EMAIL"]), "at": time.time()}

    def login():
        if time.time() - cached["at"] > 540:
            cached["token"], cached["at"] = e2e.login(env, env["LC_TEST_EMAIL"]), time.time()
        return cached["token"]

    # one label per arm run: conversations of one run share the static prefix, arms never read each other's cache
    label = args.label or f"{args.arm}-{uuid.uuid4().hex[:8]}"
    extra = {"promptPrefix": f"Run label: {label}. " + eg.PREFIX + PURPOSE}
    if args.max_context:
        extra["maxContextTokens"] = args.max_context
    run = {"commit": e2e.git_commit(args.arm), "arm": args.arm, "variant": args.arm, "provider": args.provider, "model": preset["model"],
           "effort": args.effort, "ttl": args.ttl, "label": label, "started": e2e.iso_now(), "conversations": []}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(f"{e2e.KIT}/runs-g", exist_ok=True)
    out = f"{e2e.KIT}/runs-g/{args.provider}-{args.arm}-{args.scenarios.replace(',', '')}-{stamp}.json"
    try:
        for sc in args.scenarios.split(","):
            eg.run_conversation(login, sc, eg.SC[sc], args.gap, extra, args.arm, run)
    finally:
        run["finished"] = e2e.iso_now()
        json.dump(run, open(out, "w"), indent=1)
    print("run file:", out)


if __name__ == "__main__":
    sys.exit(main())
