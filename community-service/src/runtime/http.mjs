import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { readFile } from 'node:fs/promises';

const mimeTypes = { js: 'text/javascript', css: 'text/css', html: 'text/html', svg: 'image/svg+xml', png: 'image/png', woff2: 'font/woff2' };

export function operatorAssets(root) {
  return { async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path !== '/index.html' && !/^\/assets\/[A-Za-z0-9_.-]+\.(?:js|css|woff2|png|svg)$/.test(path)) return new Response(null, { status: 404 });
    try {
      const bytes = await readFile(new URL(`.${path}`, root));
      return new Response(request.method === 'HEAD' ? null : bytes, { headers: {
        'Content-Type': mimeTypes[path.split('.').at(-1)], 'Content-Length': String(bytes.length),
      } });
    } catch { return new Response(null, { status: 404 }); }
  } };
}

function failure(status, code) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
}

function requestHeaders(incoming) {
  const headers = new Headers();
  for (let index = 0; index < incoming.rawHeaders.length; index += 2) {
    const name = incoming.rawHeaders[index];
    if (headers.has(name)) throw new Error('invalid_request');
    headers.set(name, incoming.rawHeaders[index + 1]);
  }
  return headers;
}

async function body(incoming, maximum) {
  const declared = incoming.headers['content-length'];
  if (declared !== undefined && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new Error('body_too_large');
  const chunks = [];
  let length = 0;
  for await (const chunk of incoming) {
    length += chunk.length;
    if (length > maximum) throw new Error('body_too_large');
    chunks.push(chunk);
  }
  return length ? Buffer.concat(chunks, length) : undefined;
}

export function createHttpServer(handler, configuration) {
  const origins = new Map([configuration.env.PUBLIC_ORIGIN, configuration.env.OPERATOR_ORIGIN]
    .filter(Boolean).map(value => { const url = new URL(value); return [url.host, url.origin]; }));
  const server = createServer({ maxHeaderSize: 16384, requestTimeout: 15000, headersTimeout: 10000,
    keepAliveTimeout: 5000 }, async (incoming, outgoing) => {
    const controller = new AbortController();
    incoming.on('aborted', () => controller.abort());
    outgoing.on('close', () => { if (!outgoing.writableEnded) controller.abort(); });
    let response;
    try {
      const headers = requestHeaders(incoming);
      if (!incoming.url?.startsWith('/') || incoming.url.startsWith('//') || incoming.url.includes('\\')) throw new Error('invalid_request');
      if (incoming.url === '/health' && ['GET', 'HEAD'].includes(incoming.method)) {
        response = Response.json({ status: 'ok' }, { headers: { 'Cache-Control': 'no-store' } });
      } else if (incoming.url === '/ready' && ['GET', 'HEAD'].includes(incoming.method)) {
        response = configuration.enabled && (!configuration.ready || configuration.ready()) ?
          Response.json({ status: 'ready' }, { headers: { 'Cache-Control': 'no-store' } }) : failure(503, 'service_unavailable');
      } else if (!configuration.enabled) {
        response = failure(503, 'service_unavailable');
      } else {
        const origin = origins.get(headers.get('host'));
        if (!origin) throw new Error('invalid_request');
        const url = new URL(incoming.url, origin);
        if (url.origin !== origin) throw new Error('invalid_request');
        const bytes = await body(incoming, configuration.requestBytes);
        if (['GET', 'HEAD'].includes(incoming.method) && bytes) throw new Error('invalid_request');
        const request = new Request(url, { method: incoming.method, headers, body: bytes, signal: controller.signal });
        response = await handler(request);
      }
    } catch (error) {
      const code = error.message;
      response = code === 'body_too_large' ? failure(413, code) : code === 'invalid_request' ? failure(400, code) : failure(503, 'service_unavailable');
    }
    try {
      outgoing.statusCode = response.status;
      for (const [name, value] of response.headers) {
        if (!['connection', 'transfer-encoding', 'keep-alive', 'set-cookie'].includes(name)) outgoing.setHeader(name, value);
      }
      const cookies = response.headers.getSetCookie();
      if (cookies.length) outgoing.setHeader('Set-Cookie', cookies);
      if (incoming.method === 'HEAD' || !response.body) outgoing.end();
      else await pipeline(Readable.fromWeb(response.body), outgoing);
    } catch { outgoing.destroy(); }
  });
  server.maxConnections = 128;
  server.on('clientError', (_error, socket) => socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n'));
  return server;
}