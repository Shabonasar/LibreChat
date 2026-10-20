#!/usr/bin/env bash
# usage: run.sh up <dev|stack|replay> | restart | down | status | logs [n] | create-user
#        run.sh g <anthropic|openai|kimi|zai> "<arms>" [model] [effort] [full|smoke|g2|g3]
#        run.sh tools|g4|handoff [model] ["<arms>"]     (g4 needs a Code API: LIBRECHAT_CODE_BASEURL in .env)
# Option --ttl 5m|1h (anywhere in the arguments, default 5m): promptCacheTtl of the Anthropic presets and agents.
# Trees: $SRC_ROOT/src-<arm> (default: next to this script). Keys: files in $KEYS_DIR (default ./keys): anthropic.key, openai.key, kimi.key, zai.key.
# Optional egress proxy for the capture service: export HTTPS_PROXY=... NODE_USE_ENV_PROXY=1 (passed through by docker-compose.yml).
# Optional extra compose file (e.g. a fixed network subnet): export COMPOSE_OVERRIDE=/path/override.yml.
# Real provider APIs are called: every run spends money.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$KIT"
export LC_PORT="${LC_PORT:-13184}" LC_UID="$(id -u)" LC_GID="$(id -g)"
SRC_ROOT="${SRC_ROOT:-$KIT}"
KEYS_DIR="${KEYS_DIR:-$KIT/keys}"
for p in anthropic openai kimi zai; do
  v="$(echo "$p" | tr a-z A-Z)_KEY_FILE"
  f="${!v:-$KEYS_DIR/$p.key}"
  [ -f "$f" ] || f=/dev/null
  export "$v=$f"
done
TTL=5m; ARGV=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ttl) TTL="${2:?--ttl needs 5m or 1h}"; shift 2 ;;
    *) ARGV+=("$1"); shift ;;
  esac
done
[ "$TTL" = 5m ] || [ "$TTL" = 1h ] || { echo "--ttl: 5m or 1h"; exit 1; }
set -- ${ARGV[@]+"${ARGV[@]}"}
dc() { docker compose -p lc-repro -f "$KIT/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"} "$@"; }
ready() {
  for i in $(seq 1 60); do
    python3 -I -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$LC_PORT/health',timeout=3)" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}
cleanup() { ARM=x SRC_DIR=/nonexistent dc down >/dev/null 2>&1 || true; }

case "${1:-}" in
  up)
    ARM="${2:?arm: dev|stack|replay}"
    SRC="$SRC_ROOT/src-$ARM"
    [ -d "$SRC" ] || { echo "no tree $SRC"; exit 1; }
    [ -f "$KIT/.env" ] || { echo "no .env (copy .env.example)"; exit 1; }
    mkdir -p data/logs data/uploads data/images data/mongo data/proxy/bodies
    if [ "$ARM" = dev ]; then
      # dev = upstream behaviour: no promptCache block
      grep -v -E "^    promptCache:|^      stableContext:" librechat.yaml > librechat.active.yaml
    else
      sed "s/STABLE_CONTEXT/true/" librechat.yaml > librechat.active.yaml
    fi
    chmod 600 librechat.active.yaml
    export SRC_DIR="$SRC" ARM
    dc up -d --force-recreate api capture mcp-big web-stub
    echo "$ARM" > "$KIT/.current-arm"
    ready && { echo "ready: $ARM"; exit 0; }
    echo "not ready"; docker logs --tail 40 lc-repro-api; exit 1 ;;
  restart) docker restart lc-repro-api >/dev/null; ready && echo "restarted, ready" || { echo "not ready"; exit 1; } ;;
  down) cleanup ;;
  status) docker ps --filter name=lc-repro- --format '{{.Names}} {{.Status}} {{.Ports}}'; cat "$KIT/.current-arm" 2>/dev/null || true ;;
  logs) docker logs --tail "${2:-80}" lc-repro-api ;;
  create-user)
    set -a; . "$KIT/.env"; set +a
    docker exec lc-repro-api node config/create-user.js "$LC_TEST_EMAIL" "measure" "measure" "$LC_TEST_PASSWORD" --email-verified=true ;;
  g)
    PROVIDER="${2:?provider}"; ARMS="${3:?arms, e.g. \"dev stack replay\"}"; MODEL="${4:-}"; EFFORT="${5:-low}"; SET="${6:-full}"
    trap cleanup EXIT
    mkdir -p logs runs-g results-g
    STAMP=$(date +%Y%m%d-%H%M%S)
    touch "logs/.g-start-$STAMP"
    for ARM in $ARMS; do
      echo "=== $PROVIDER $ARM"
      "$0" up "$ARM"
      "$0" create-user >/dev/null 2>&1 || true
      LABEL="$ARM-$(python3 -I -c 'import uuid; print(uuid.uuid4().hex[:8])')"
      G="python3 -I run_g.py --arm $ARM --provider $PROVIDER --effort $EFFORT --ttl $TTL --label $LABEL ${MODEL:+--model $MODEL}"
      if [ "$SET" = smoke ]; then
        $G --scenarios G1B 2>&1 | tee "logs/g-$PROVIDER-$ARM-smoke-$STAMP.log"
      elif [ "$SET" = g2 ]; then
        $G --scenarios G2,G2O1,G2O2 2>&1 | tail -3
      elif [ "$SET" = g3 ]; then
        $G --scenarios G3 --max-context "${G3_CTX:-12000}" 2>&1 | tail -3
      else
        # the three groups run in parallel in separate conversations; the G1 gap keeps the 5 minute cache TTL honest
        $G --scenarios G1,G1B --gap 65 > "logs/g-$PROVIDER-$ARM-1-$STAMP.log" 2>&1 &
        $G --scenarios G2,G2O1,G2O2 > "logs/g-$PROVIDER-$ARM-2-$STAMP.log" 2>&1 &
        $G --scenarios G3 --max-context "${G3_CTX:-12000}" > "logs/g-$PROVIDER-$ARM-3-$STAMP.log" 2>&1 &
        wait
        tail -n 3 logs/g-$PROVIDER-$ARM-*-$STAMP.log
      fi
    done
    FILES=$(find runs-g -name "$PROVIDER-*.json" -newer "logs/.g-start-$STAMP" 2>/dev/null || true)
    python3 -I analyze_g.py $FILES --md "results-g/$PROVIDER-$SET-$STAMP.md" | head -20
    echo "results: results-g/$PROVIDER-$SET-$STAMP.md" ;;
  tools|g4|handoff)
    SC="$1"; MODEL="${2:-claude-haiku-5-5}"; ARMS="${3:-dev stack replay}"
    if [ "$SC" = g4 ]; then
      grep -q "^LIBRECHAT_CODE_BASEURL=." "$KIT/.env" || { echo "g4: set LIBRECHAT_CODE_BASEURL (and the keys your Code API needs) in .env"; exit 1; }
    fi
    trap cleanup EXIT
    for ARM in $ARMS; do
      echo "=== $SC $ARM"
      "$0" up "$ARM"
      "$0" create-user >/dev/null 2>&1 || true
      case "$SC" in
        tools) python3 -I run_tools.py --arm "$ARM" --model "$MODEL" --ttl "$TTL" --scenario tools ;;
        g4) python3 -I run_g4.py --arm "$ARM" --model "$MODEL" --ttl "$TTL" ;;
        handoff) python3 -I run_handoff.py --arm "$ARM" --model "$MODEL" --ttl "$TTL" ;;
      esac
    done
    ls -t results-$SC/*-"$MODEL"-*.md | head -3 ;;
  *) echo "usage: see header"; exit 1 ;;
esac
