import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accessToken, operatorClient, operatorOrigin } from '../../scripts/operator/transport.mjs';

const origin = 'https://operator.example.test';
const token = 'secret.header.signature';

test('human token retrieval uses scoped cloudflared arguments and suppresses child errors', async () => {
  assert.equal(await accessToken(origin, async (command, args) => {
    assert.equal(command, 'cloudflared');
    assert.deepEqual(args, ['access', 'token', `-app=${origin}/admin/`]);
    return { stdout: `${token}\n` };
  }), token);
  await assert.rejects(accessToken(origin, async () => { throw new Error(token); }), /access_login_required/);
  await assert.rejects(accessToken(origin, async () => ({ stdout: 'invalid' })), /access_login_required/);
});

test('transport preserves the browser mutation boundary without credentials in the URL', async () => {
  const request = operatorClient(origin, token, async (url, options) => {
    assert.equal(url.href, `${origin}/admin/v1/moderation`);
    assert.equal(options.headers['Cf-Access-Token'], token);
    assert.equal(options.headers.Origin, origin);
    assert.equal(options.headers['X-Community-Operator'], '1');
    assert.equal(options.headers['Sec-Fetch-Site'], undefined);
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.body, '{"version":1}');
    return Response.json({ version: 1 });
  });
  assert.deepEqual(await request('/admin/v1/moderation', { version: 1 }), { version: 1 });
});

test('rejects origins and paths that could leak credentials', async () => {
  for (const value of ['http://operator.example.test', `${origin}/`, `${origin}/admin/`,
    'https://user:password@operator.example.test', 'https://app.workers.dev'])
    assert.throws(() => operatorOrigin(value), /invalid_operator_origin/);
  const request = operatorClient(origin, token, () => assert.fail('must not fetch'));
  for (const path of ['https://hostile.test/admin/v1/session', '//hostile.test', '/admin/v1/../session'])
    await assert.rejects(request(path), /invalid_operator_path/);
});

test('fails closed on redirects, HTML, malformed UTF8, oversized and secret-bearing errors', async () => {
  for (const response of [new Response(null, { status: 302 }), new Response('<html>login</html>'),
    new Response(new Uint8Array([255]), { headers: { 'content-type': 'application/json' } }),
    Response.json({ value: 'x'.repeat(131072) }), Response.json({ error: { code: token } }, { status: 401 })]) {
    await assert.rejects(operatorClient(origin, token, async () => response)('/admin/v1/session'), error => {
      assert.ok(!error.message.includes(token));
      return true;
    });
  }
  await assert.rejects(operatorClient(origin, token, async () => { throw new Error(token); })('/admin/v1/session'),
    /operator_transport_unavailable/);
  await assert.rejects(operatorClient(origin, token, async () => Response.json(
    { error: { code: 'operator_not_allowed' } }, { status: 403 }))('/admin/v1/session'),
    error => error.status === 403 && error.code === 'operator_not_allowed');
});