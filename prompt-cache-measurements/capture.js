// Multi-provider logging proxy between LibreChat and the provider APIs. One listener per provider (ROUTES), forwards everything
// unchanged, stores one record per model request with usage normalized to Anthropic names
// (input_tokens = uncached prompt, cache_read_input_tokens, cache_creation_input_tokens, output_tokens).
// Request headers are never stored (they carry API keys).
const http = require('http');
const https = require('https');
const fs = require('fs');
const crypto = require('crypto');
const { URL } = require('url');

const OUT = process.env.OUT_DIR || './data/proxy';
fs.mkdirSync(`${OUT}/bodies`, { recursive: true });
const sha = (v) => crypto.createHash('sha256').update(typeof v === 'string' ? v : JSON.stringify(v ?? null)).digest('hex').slice(0, 12);
const redact = (s) => s.replace(/(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9]{32}\.[A-Za-z0-9]{16})/g, '[redacted]');
const prev = new Map();

function firstDiff(a, b) {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

function commonMessages(pa, pb) {
  let i = 0;
  while (i < pa.length && i < pb.length && JSON.stringify(pa[i]) === JSON.stringify(pb[i])) i++;
  return i;
}

const isSystem = (m) => m && (m.role === 'system' || m.role === 'developer');
const textOf = (c) => (typeof c === 'string' ? c : Array.isArray(c) ? c.map((b) => b.text || '').join('') : '');

function shape(kind, j) {
  if (kind === 'messages') {
    const msgs = j.messages || [];
    const sys = typeof j.system === 'string' ? j.system : (j.system || []).map((b) => b.text || '').join('');
    const think = msgs.reduce((a, m) => a + (Array.isArray(m.content) ? m.content.filter((b) => b.type === 'thinking' || b.type === 'redacted_thinking').length : 0), 0);
    return { system: j.system, systemText: sys, msgs, think };
  }
  if (kind === 'responses') {
    const items = typeof j.input === 'string' ? [{ role: 'user', content: j.input }] : j.input || [];
    const sys = items.filter(isSystem);
    return {
      system: [j.instructions || null, sys],
      systemText: (j.instructions || '') + sys.map((m) => textOf(m.content)).join(''),
      msgs: items.filter((m) => !isSystem(m)),
      think: items.filter((m) => m.type === 'reasoning').length,
    };
  }
  const msgs = j.messages || [];
  const sys = msgs.filter(isSystem);
  return {
    system: sys,
    systemText: sys.map((m) => textOf(m.content)).join(''),
    msgs: msgs.filter((m) => !isSystem(m)),
    think: msgs.filter((m) => m.reasoning_content || m.reasoning).length,
  };
}

function sseData(text) {
  const out = [];
  if (text.trimStart().startsWith('{')) {
    try { out.push({ full: JSON.parse(text) }); } catch (e) { /* error body */ }
    return out;
  }
  for (const line of text.split('\n')) {
    if (!line.startsWith('data:')) continue;
    const d = line.slice(5).trim();
    if (d && d !== '[DONE]') try { out.push(JSON.parse(d)); } catch (e) { /* partial line */ }
  }
  return out;
}

function parseAnthropic(events) {
  const out = { usage: {}, input_transformations: null, stop_reason: null, thinking_blocks: 0, signed_thinking_blocks: 0 };
  for (const ev of events) {
    const m = ev.full || (ev.type === 'message_start' ? ev.message : null);
    if (m) {
      Object.assign(out.usage, m.usage || {});
      if (m.input_transformations !== undefined) out.input_transformations = m.input_transformations;
      if (m.stop_reason) out.stop_reason = m.stop_reason;
      for (const b of m.content || []) if (b.type === 'thinking') { out.thinking_blocks++; if (b.signature) out.signed_thinking_blocks++; }
    }
    if (ev.type === 'message_delta') {
      Object.assign(out.usage, ev.usage || {});
      if (ev.delta && ev.delta.stop_reason) out.stop_reason = ev.delta.stop_reason;
      if (ev.input_transformations !== undefined) out.input_transformations = ev.input_transformations;
    }
    if (ev.type === 'content_block_start' && ev.content_block.type === 'thinking') out.thinking_blocks++;
    if (ev.type === 'content_block_delta' && ev.delta.type === 'signature_delta') out.signed_thinking_blocks++;
  }
  return out;
}

function normalized(prompt, cached, output, reasoning) {
  return { input_tokens: Math.max(0, prompt - cached), cache_read_input_tokens: cached, cache_creation_input_tokens: 0, output_tokens: output, reasoning_tokens: reasoning };
}

function parseResponses(events) {
  const out = { usage: {}, stop_reason: null, thinking_blocks: 0, signed_thinking_blocks: 0 };
  for (const ev of events) {
    const r = ev.full || (ev.type === 'response.completed' || ev.type === 'response.incomplete' ? ev.response : null);
    if (!r) continue;
    const u = r.usage || {};
    out.usage = normalized(u.input_tokens || 0, (u.input_tokens_details || {}).cached_tokens || 0, u.output_tokens || 0, (u.output_tokens_details || {}).reasoning_tokens || 0);
    out.stop_reason = r.status;
    for (const it of r.output || []) if (it.type === 'reasoning') { out.thinking_blocks++; if (it.encrypted_content) out.signed_thinking_blocks++; }
  }
  return out;
}

function parseChat(events) {
  const out = { usage: {}, stop_reason: null, thinking_blocks: 0, signed_thinking_blocks: 0 };
  let thought = false;
  for (const ev of events) {
    const c = ev.full || ev;
    const ch = (c.choices || [])[0] || {};
    const d = ch.delta || ch.message || {};
    if (d.reasoning_content || d.reasoning) thought = true;
    if (ch.finish_reason) out.stop_reason = ch.finish_reason;
    const u = c.usage || ch.usage;
    if (u) {
      const cached = (u.prompt_tokens_details || {}).cached_tokens ?? u.cached_tokens ?? 0;
      out.usage = normalized(u.prompt_tokens || 0, cached, u.completion_tokens || 0, (u.completion_tokens_details || {}).reasoning_tokens || 0);
    }
  }
  if (thought) out.thinking_blocks = 1;
  return out;
}

const PARSERS = { messages: parseAnthropic, responses: parseResponses, chat: parseChat };

function kindOf(url) {
  const p = url.split('?')[0];
  if (p.endsWith('/messages')) return 'messages';
  if (p.endsWith('/responses')) return 'responses';
  if (p.endsWith('/chat/completions')) return 'chat';
  return null;
}

function serve(port, provider, upstream) {
  const target = new URL(upstream);
  http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks);
      const rid = crypto.randomBytes(6).toString('hex');
      const kind = req.method === 'POST' ? kindOf(req.url) : null;
      let rec = null;
      if (kind) {
        try {
          const j = JSON.parse(raw.toString('utf8'));
          const conv = req.headers['x-lc-conversation'] || '';
          const p = prev.get(conv);
          const s = shape(kind, j);
          rec = {
            rid, ts: new Date().toISOString(), conv, arm: req.headers['x-lc-arm'] || '', provider, kind,
            model: j.model, beta: req.headers['anthropic-beta'] || null,
            thinking: j.thinking || j.reasoning || j.reasoning_effort || null,
            system_sha: sha(s.system), system_head: s.systemText.slice(0, 40), label_in_system: s.systemText.includes('Run label:'), tools_sha: sha(j.tools), tools_n: (j.tools || []).length,
            messages_n: s.msgs.length, body_bytes: raw.length, history_thinking_blocks: s.think,
            first_diff_byte: p ? firstDiff(p.raw, raw) : null,
            prev_body_bytes: p ? p.raw.length : null,
            common_messages: p ? commonMessages(p.msgs, s.msgs) : null,
            system_same: p ? p.system === sha(s.system) : null,
            tools_same: p ? p.tools === sha(j.tools) : null,
          };
          prev.set(conv, { raw, msgs: s.msgs, system: sha(s.system), tools: sha(j.tools) });
          fs.writeFileSync(`${OUT}/bodies/${rid}.json`, raw);
        } catch (e) {
          rec = { rid, ts: new Date().toISOString(), provider, parse_error: String(e.message) };
        }
      }
      const headers = { ...req.headers, host: target.host, 'content-length': raw.length };
      delete headers['transfer-encoding'];
      // usage is parsed from the body, so ask for an uncompressed response
      headers['accept-encoding'] = 'identity';
      const lib = target.protocol === 'https:' ? https : http;
      const up = lib.request(
        { hostname: target.hostname, port: target.port || (target.protocol === 'https:' ? 443 : 80), path: req.url, method: req.method, headers },
        (ur) => {
          res.writeHead(ur.statusCode, ur.headers);
          const rc = [];
          ur.on('data', (c) => { rc.push(c); res.write(c); });
          ur.on('end', () => {
            res.end();
            if (!rec) return;
            const text = Buffer.concat(rc).toString('utf8');
            rec.status = ur.statusCode;
            rec.request_id = ur.headers['request-id'] || ur.headers['x-request-id'] || null;
            if (ur.statusCode === 200 && kind) Object.assign(rec, PARSERS[kind](sseData(text)));
            else rec.error = redact(text).slice(0, 800);
            fs.appendFileSync(`${OUT}/requests.jsonl`, JSON.stringify(rec) + '\n');
            fs.writeFileSync(`${OUT}/bodies/${rid}.response.txt`, redact(text));
          });
        },
      );
      up.on('error', (e) => {
        res.writeHead(502);
        res.end(String(e.message));
        if (rec) fs.appendFileSync(`${OUT}/requests.jsonl`, JSON.stringify({ ...rec, status: 502, error: String(e.message) }) + '\n');
      });
      up.end(raw);
    });
  }).listen(port, '0.0.0.0');
}

// ROUTES: "8080=anthropic=https://api.anthropic.com,8081=openai=https://api.openai.com,8082=kimi=<BASE_URL>,..." (origin only, the path comes from the LibreChat baseURL)
for (const r of (process.env.ROUTES || '').split(',').filter(Boolean)) {
  const [port, provider, upstream] = r.split('=');
  serve(Number(port), provider, upstream);
}
