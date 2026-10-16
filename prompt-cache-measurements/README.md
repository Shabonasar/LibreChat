# Prompt-cache measurement kit

Repeats the live prompt-cache measurements of the LibreChat prompt-cache PR series (tracking issue LibreChat-AI/LibreChat#17102): the same conversations are run against several LibreChat builds, every model request is recorded by a logging proxy, and the logs are summarized per arm. Real provider APIs are called, so every run costs money. Stdlib-only Python 3, Docker with compose, Node 24 (in containers).

## What is measured

All numbers come from the provider usage records captured between LibreChat and the API (usage is normalized to Anthropic names for the OpenAI-style APIs).

- cached share = cache read tokens / prompt tokens, summed over the main requests of a conversation (main = request with a system prompt; the summarizer request has none and is counted separately).
- miss = a main request that reads less than 98% of what the previous main request had cached (cache read + cache creation).
- uncached tokens = prompt tokens - cache read tokens (cache writes count as uncached).
- also recorded per request: thinking blocks in history, `input_transformations`, whether system/tools are byte-identical to the previous request, first differing byte of the request body.

## Arms

| arm | tree | config |
|---|---|---|
| dev | upstream `dev` | no `promptCache` block |
| stack | the PR series | `endpoints.agents.promptCache.stableContext: true` |
| replay | the PR series plus reasoning replay | `stableContext: true` |

An arm is a LibreChat checkout in `$SRC_ROOT/src-<arm>` (default: next to the scripts). `run.sh` removes the `promptCache` block from the config for `dev` and sets `stableContext: true` for the others. Each run gets a unique `Run label:` in the system prompt, so arms never read each other's cache.

Claude Haiku 5.5 needs LibreChat-AI/LibreChat#17062 applied on every arm, including dev: upstream does not recognize Haiku 5.5 (`thinking.type: enabled` is sent and rejected with 400, and thinking blocks are not bound).

## Setup

1. Trees. For each arm:
   ```
   git clone <LIBRECHAT_REPO> src-<arm> && git -C src-<arm> checkout <LIBRECHAT_BRANCH>
   AGENTS_SDK=<AGENTS_SDK_DIR> ./build.sh <arm>
   ```
   `<AGENTS_SDK_DIR>` is a built `@librechat/agents` checkout (package.json, dist, node_modules) that replaces the package after `npm ci`; omit it to use the version from the tree. `<LIBRECHAT_BRANCH>` is the branch of the arm (upstream dev, series head, series head with replay) plus #17062 for Haiku 5.5.
2. Config: `cp .env.example .env`, fill secrets and `LC_TEST_PASSWORD`. For Kimi / Z.ai set `KIMI_BASE_URL` / `ZAI_BASE_URL` (origin) in `.env` and put the path part into `librechat.yaml` instead of `<KIMI_PATH>` / `<ZAI_PATH>`.
3. Keys: one file per provider in `./keys` (`anthropic.key`, `openai.key`, `kimi.key`, `zai.key`; or `*_KEY_FILE` variables). They are mounted read-only and exported inside the container at process start, never written to env files or yaml. Only providers you measure are needed.
4. Optional egress proxy: `export HTTPS_PROXY=http://<PROXY> NODE_USE_ENV_PROXY=1` before `run.sh`; only the logging proxy container uses it.
5. If docker reports "could not find an available, non-overlapping IPv4 address pool", give the stand a fixed subnet: a file with `networks: {default: {ipam: {config: [{subnet: 172.46.0.0/24}]}}}` and `export COMPOSE_OVERRIDE=<that file>`.

Topology (`docker-compose.yml`): LibreChat (`api`) -> `capture` (`capture.js`, one port per provider, forwards unchanged, never stores request headers) -> provider API; Mongo; `mcp-big` (`mcp-big.cjs`, MCP server whose `fetch_report` tool returns a ~9000 character report; the same server is registered as `bigtool`, `bigtool2`, `bigtool3`); `web-stub` (web search backend that answers nothing, only the tool definition matters). Anthropic goes through `ANTHROPIC_REVERSE_PROXY`, OpenAI (Responses API), Kimi and Z.ai are OpenAI-compatible custom endpoints in `librechat.yaml` pointing at `capture`.

Records: `data/proxy/requests.jsonl` (one line per model request), `data/proxy/bodies/` (request and redacted response bodies).

## Scenarios

All prompts avoid asking the model about its own reasoning (such prompts get refused with `stop_details.category: reasoning_extraction`) and avoid rate-limit-evasion wording (refused with category `cyber`).

- G1: plain chat, 10 turns, system prompt contains `{{current_datetime}}`, 65 s gap between turns.
- G1B: 3 turns picked from the G1 prompts, no gap.
- G2: 8 turns with web search and three MCP servers toggled per turn (different subsets and order, one turn without web search); G2O1 / G2O2: 2 turns with all three servers enabled in a different order.
- G3: compaction, 12 turns with tool calls and growing history, `maxContextTokens` 12000 (default) or 32000 (`G3_CTX=32000`) so summarization triggers.
- tools: 8 turns, Anthropic only, thinking on. The model plans and calls `fetch_report` of `bigtool` on turns 2, 4 and 5 (several calls per turn); LibreChat is restarted after turn 4.

## Run

```
./run.sh g anthropic "dev stack replay" claude-haiku-5-5 low full      # G1, G1B, G2*, G3 for every arm
./run.sh g openai "dev stack" gpt-5.4-nano low g3                       # one set: full | smoke | g2 | g3
./run.sh g kimi "dev stack"; ./run.sh g zai "dev stack"
./run.sh tools claude-haiku-5-5 "dev stack replay"                      # "tools" scenario
```

`run.sh up <arm>` / `restart` / `down` / `status` / `logs` / `create-user` are available for manual work. Run files go to `runs-g/` and `runs-tools/`, summaries to `results-g/` and `results-tools/` (written by the run itself).

## Analyze

```
python3 -I analyze_g.py runs-g/<file>.json ... --md results-g/out.md     # one row per conversation: share, misses, uncached, output, cost; per-request detail below
python3 -I analyze_tools.py runs-tools/<file>.json                        # per-request table of the tools run
```

`PRICES` in `analyze_g.py` is a small price table for the cost column; adjust it to the current price list. Compare arms on the same provider, model and effort; read share, misses and uncached together (a high share on a larger prompt can still mean more uncached tokens).

## Files

`run.sh` runner and stand control, `build.sh` tree build, `docker-compose.yml`, `librechat.yaml`, `.env.example`, `capture.js` logging proxy, `mcp-big.cjs` MCP server, `e2e.py` LibreChat HTTP helpers, `g_scenarios.py` G scenarios, `run_g.py` G runner, `run_tools.py` tools scenario, `analyze_g.py`, `analyze_tools.py`.
