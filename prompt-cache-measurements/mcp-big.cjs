// Measurement-only MCP server: one tool that returns a large text, to exercise tool output fading.
const http = require('http');
const crypto = require('crypto');
const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StreamableHTTPServerTransport } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { z } = require('zod');

const CHARS = Number(process.env.REPORT_CHARS || 9000);

function buildReport(topic) {
  const id = crypto.randomBytes(6).toString('hex');
  const lines = [`Report on ${topic}, id ${id}`];
  let i = 0;
  while (lines.join('\n').length < CHARS) {
    const h = crypto.createHash('sha1').update(`${topic}:${id}:${i}`).digest('hex');
    lines.push(`row ${i}: metric ${h.slice(0, 8)} value ${parseInt(h.slice(8, 14), 16) % 10000} status ${h.slice(14, 18)} note ${h.slice(18, 40)}`);
    i++;
  }
  return lines.join('\n');
}

function server() {
  const s = new McpServer({ name: 'big-report', version: '1.0.0' });
  s.tool('fetch_report', 'Fetch the full data report for a topic. Returns a long table of rows.', { topic: z.string() }, async ({ topic }) => ({
    content: [{ type: 'text', text: buildReport(topic) }],
  }));
  return s;
}

http
  .createServer(async (req, res) => {
    if (req.url !== '/mcp') {
      res.writeHead(404).end();
      return;
    }
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', async () => {
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined;
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      const s = server();
      res.on('close', () => {
        transport.close();
        s.close();
      });
      await s.connect(transport);
      await transport.handleRequest(req, res, body);
    });
  })
  .listen(3000, '0.0.0.0');
