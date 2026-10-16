#!/usr/bin/env python3
"""LibreChat HTTP helpers: login, send one chat turn through /api/agents/chat and wait until it finishes. Stdlib only."""
import datetime
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

KIT = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.environ.get("SRC_ROOT", KIT)  # trees are SRC_ROOT/src-<arm>
BASE = f"http://127.0.0.1:{os.environ.get('LC_PORT', '13184')}"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
NO_PARENT = "00000000-0000-0000-0000-000000000000"
TURN_TIMEOUT = 420

# preset: endpoint-specific request fields, set by the runner; mcp: server names enabled for the turn; web: web search on/off
PROVIDER = {"preset": {}, "endpoint": "anthropic", "mcp": [], "web": False}


def load_env():
    env = {}
    for line in open(os.environ.get("ENV_FILE", f"{KIT}/.env")):
        if "=" in line and not line.startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v
    return env


def git_commit(arm):
    try:
        return subprocess.run(["git", "-C", f"{SRC_ROOT}/src-{arm}", "rev-parse", "--short=9", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def request(method, path, data=None, token=None, timeout=60):
    headers = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as err:
        return err.code, err.read()[:500].decode(errors="replace")


def login(env, email):
    status, data = request("POST", "/api/auth/login", {"email": email, "password": env["LC_TEST_PASSWORD"]})
    if status != 200:
        raise SystemExit(f"login failed: {status}")
    return data["token"]


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def send_turn(token, conv_id, parent_id, text, artifacts, preset=None):
    msg_id = str(uuid.uuid4())
    ephemeral = {"mcp": list(PROVIDER.get("mcp", [])), "web_search": bool(PROVIDER.get("web")), "file_search": False, "execute_code": False}
    if artifacts:
        ephemeral["artifacts"] = "default"
    body = {
        **PROVIDER["preset"],
        **(preset or {}),
        "text": text,
        "sender": "User",
        "clientTimestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "isCreatedByUser": True,
        "parentMessageId": parent_id,
        "conversationId": conv_id,
        "messageId": msg_id,
        "error": False,
        "key": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
        "ephemeralAgent": ephemeral,
        "isTemporary": False,
        "timezone": "UTC",
        "clientRequestId": str(uuid.uuid4()),
    }
    path = "/api/agents/chat/" + urllib.parse.quote(PROVIDER["endpoint"], safe="")
    t0 = iso_now()
    status, data = request("POST", path, body, token)
    if status != 200:
        raise RuntimeError(f"chat start failed {status}: {data}")
    deadline = time.time() + TURN_TIMEOUT
    time.sleep(2)
    while time.time() < deadline:
        s, st = request("GET", f"/api/agents/chat/status/{conv_id}", None, token)
        if s == 200 and isinstance(st, dict) and not st.get("active"):
            break
        time.sleep(2)
    else:
        raise RuntimeError("turn timeout")
    reply = None
    unfinished = None
    for attempt in range(30):
        s, msgs = request("GET", f"/api/messages/{conv_id}", None, token)
        if isinstance(msgs, list):
            user_msg = next((m for m in msgs if m.get("isCreatedByUser") and text in (m.get("text") or "")), None)
            if user_msg is not None:
                msg_id = user_msg["messageId"]
                replies = [m for m in msgs if m.get("parentMessageId") == msg_id and not m.get("isCreatedByUser")]
                reply = next((m for m in replies if not m.get("unfinished")), None)
                unfinished = next(iter(replies), None)
        if reply is not None:
            break
        # a turn ended as incomplete (tool call limit) stays unfinished
        if unfinished is not None and attempt >= 5:
            reply = unfinished
            break
        time.sleep(2)
    if reply is None:
        shape = [(m.get("isCreatedByUser"), m.get("parentMessageId") == msg_id, m.get("unfinished")) for m in msgs] if isinstance(msgs, list) else msgs
        raise RuntimeError(f"no assistant message: {shape}")
    return {
        "user_message_id": msg_id,
        "assistant_message_id": reply["messageId"],
        "t_start": t0,
        "t_end": iso_now(),
        "reply_chars": len(json.dumps(reply.get("content") or reply.get("text") or "")),
        "error": bool(reply.get("error")),
        "unfinished": bool(reply.get("unfinished")),
    }
