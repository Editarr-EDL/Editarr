import assert from 'node:assert/strict';
import { test } from 'node:test';
import { once } from 'node:events';
import { request as httpRequest } from 'node:http';
import { createHttpServer, operatorAssets } from '../../src/runtime/http.mjs';

function send(url, options, bytes) {
  return new Promise((resolve, reject) => {
    const request = httpRequest(url, options, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode,
        text: Buffer.concat(chunks).toString(), cookies: response.headers['set-cookie'] }));
    });
    request.on('error', reject);
    request.end(bytes);
  });
}

async function running(enabled, handler, check) {
  const server = createHttpServer(handler, { enabled, requestBytes: 4, env: { PUBLIC_ORIGIN: 'https://community.test' } });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try { await check(`http://127.0.0.1:${server.address().port}`); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}

test('liveness works while readiness and API remain disabled', async () => {
  await running(false, () => { throw new Error('must_not_dispatch'); }, async origin => {
    assert.equal((await fetch(`${origin}/health`)).status, 200);
    assert.equal((await fetch(`${origin}/ready`)).status, 503);
    assert.equal((await fetch(`${origin}/v1/identity`)).status, 503);
  });
});

test('preserves configured HTTPS authority, exact bodies and multiple cookies', async () => {
  await running(true, request => {
    assert.equal(request.url, 'https://community.test/v1/identity');
    assert.equal(request.headers.get('content-digest'), 'original-digest');
    const headers = new Headers();
    headers.append('Set-Cookie', 'one=1; Secure'); headers.append('Set-Cookie', 'two=2; Secure');
    return request.text().then(value => new Response(value, { headers }));
  }, async origin => {
    const response = await send(`${origin}/v1/identity`, { method: 'POST', headers: {
      Host: 'community.test', 'Content-Digest': 'original-digest', 'Content-Length': '4' } }, 'ABCD');
    assert.equal(response.status, 200);
    assert.equal(response.text, 'ABCD');
    assert.deepEqual(response.cookies, ['one=1; Secure', 'two=2; Secure']);
  });
});

test('refuses unexpected hosts, oversized bodies and duplicate security headers', async () => {
  await running(true, () => { throw new Error('must_not_dispatch'); }, async origin => {
    assert.equal((await fetch(`${origin}/v1/identity`)).status, 400);
    assert.equal((await send(`${origin}/v1/identity`, { method: 'POST', headers: {
      Host: 'community.test', 'Content-Length': '5' } }, 'ABCDE')).status, 413);
    const status = await new Promise((resolve, reject) => {
      const request = httpRequest(`${origin}/v1/identity`, { headers: {
        Host: 'community.test', 'CF-Access-Jwt-Assertion': ['one', 'two'],
      } }, response => { response.resume(); resolve(response.statusCode); });
      request.on('error', reject); request.end();
    });
    assert.equal(status, 400);
  });
});

test('operator asset binding never serves traversals or unknown files', async () => {
  const assets = operatorAssets(new URL('./', import.meta.url));
  assert.equal((await assets.fetch(new Request('https://community.test/environment.test.mjs'))).status, 404);
  assert.equal((await assets.fetch(new Request('https://community.test/assets/%2e%2e/environment.test.mjs'))).status, 404);
});
test('bounds chunked bodies and refuses absent Host and duplicate signature headers', async () => {
  await running(true, () => { throw new Error('must_not_dispatch'); }, async origin => {
    const oversized = await send(`${origin}/v1/identity`, { method: 'POST', headers: {
      Host: 'community.test', 'Transfer-Encoding': 'chunked' } }, 'ABCDE');
    assert.equal(oversized.status, 413);
    assert.equal((await send(`${origin}/v1/identity`, { setHost: false }, undefined)).status, 400);
    for (const header of ['Signature', 'Signature-Input', 'Content-Digest', 'CF-Connecting-IP', 'Host']) {
      assert.equal((await send(`${origin}/v1/identity`, { headers: [
        ...(header === 'Host' ? [] : ['Host', 'community.test']), header, 'one', header, 'two',
      ] }, undefined)).status, 400);
    }
  });
});
