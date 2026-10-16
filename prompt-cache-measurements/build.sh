#!/usr/bin/env bash
# usage: [AGENTS_SDK=<AGENTS_SDK_DIR>] [SRC_ROOT=...] build.sh <arm>
# Builds the tree $SRC_ROOT/src-<arm> (a checkout of the LibreChat branch for this arm) inside a node container.
# AGENTS_SDK: a built @librechat/agents checkout (package.json, dist, node_modules); replaces node_modules/@librechat/agents after npm ci.
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${1:?arm: dev|stack|replay}"
SRC="${SRC_ROOT:-$KIT}/src-$NAME"
[ -d "$SRC" ] || { echo "no tree $SRC"; exit 1; }
mkdir -p "$KIT/logs" "$KIT/.npm-cache"
SDK_MOUNT=()
[ -n "${AGENTS_SDK:-}" ] && SDK_MOUNT=(-v "$AGENTS_SDK":/sdk:ro)
docker run --rm --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e npm_config_cache=/npmcache -e npm_config_fetch_retries=5 -e npm_config_fetch_retry_mintimeout=5000 \
  -e NODE_OPTIONS=--max-old-space-size=8192 \
  "${SDK_MOUNT[@]}" \
  -v "$SRC":/app -v "$KIT/.npm-cache":/npmcache -w /app node:24-alpine sh -ec '
    n=0; until npm ci --no-audit --no-fund; do n=$((n+1)); [ $n -ge 3 ] && exit 1; echo "npm ci retry $n"; done
    if [ -d /sdk/dist ]; then
      A=node_modules/@librechat/agents
      rm -rf "$A/dist" "$A/package.json"
      cp -r /sdk/dist "$A/dist"; cp /sdk/package.json "$A/package.json"
      [ -d /sdk/node_modules ] && { rm -rf "$A/node_modules"; cp -rL /sdk/node_modules "$A/node_modules"; }
      echo "agents sdk replaced from /sdk"
    fi
    npm run build:data-provider
    npm run build:data-schemas
    npm run build:api
    npm run build:client-package
    mkdir -p client/dist
    [ -f client/dist/index.html ] || echo "<!doctype html><html><head></head><body>stub</body></html>" > client/dist/index.html
  ' 2>&1 | tee "$KIT/logs/build-$NAME.log"
