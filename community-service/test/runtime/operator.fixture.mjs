import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { journey, expectStatus } from './journey.fixture.mjs';
import { run } from '../../scripts/operator-cli.mjs';

export async function exercise(database, runtime) {
  await journey(database, async ({ enroll, admin, adminToken, send, env, read }) => {
    const person = await enroll('1554889314549243999');
    const html = await admin('/admin/'); expectStatus(html);
    assert.match(html.headers['content-type'], /text\/html/);
    assert.match(html.headers['content-security-policy'], /frame-ancestors 'none'/);
    const script = html.text.match(/src="([^"]+\.js)"/)[1];
    expectStatus(await admin(script));
    expectStatus(await admin(script, undefined, { 'Cf-Access-Jwt-Assertion': '' }), 401);
    const token = await adminToken();
    const dependencies = { token: async () => token, fetcher: async (url, options) => {
      // Synthetic Access edge: exchange the human token for the verified assertion.
      assert.equal(options.headers['Cf-Access-Token'], token);
      const headers = new Headers(options.headers);
      headers.delete('Cf-Access-Token'); headers.set('Cf-Access-Jwt-Assertion', token);
      const response = await send(new Request(url, { ...options, headers }));
      return new Response(response.text, { status: response.status, headers: response.headers });
    } };
    const cli = args => run([...args, '--origin', env.OPERATOR_ORIGIN], dependencies);
    assert.equal((await cli(['status'])).providerUsage.providerVerified, false);
    assert.ok((await cli(['list', 'contributors'])).items.some(item => item.contributorId === person.contributorId));
    const current = await cli(['inspect', 'contributor', person.contributorId]);
    const directory = mkdtempSync(join(tmpdir(), 'community-cli-'));
    try {
      const action = ['moderate', 'suspend', person.contributorId, '--expected-revision', String(current.revision), '--reason', 'Synthetic operator review'];
      assert.equal((await cli(action)).dryRun, true);
      expectStatus(await read(person, '/v1/identity'));
      const checkpoint = join(directory, 'action');
      const fetcher = dependencies.fetcher;
      let lose = true;
      dependencies.fetcher = async (url, options) => {
        const response = await fetcher(url, options);
        if (lose && options.method === 'POST') { lose = false; throw new Error('synthetic lost response'); }
        return response;
      };
      await assert.rejects(cli([...action, '--confirm', '--checkpoint', checkpoint]), /operator_transport_unavailable/);
      const saved = JSON.parse(readFileSync(join(checkpoint, 'state.json'), 'utf8'));
      assert.equal(saved.state, 'pending');
      assert.ok(!JSON.stringify(saved).includes(token));
      const retried = await cli(['retry', '--checkpoint', checkpoint, '--confirm']);
      assert.equal(retried.result.actionId, saved.body.actionId);
      assert.equal(retried.result.outcome, 'applied');
      expectStatus(await read(person, '/v1/identity'), 401);
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM moderation_actions WHERE action_id = ?').bind(saved.body.actionId).first('n'), 1);
      const page = await cli(['page', 'audit', '--checkpoint', join(directory, 'pages')]);
      assert.equal(page.complete, true);
      assert.equal(page.page.items.length, 1);
    } finally { rmSync(directory, { recursive: true, force: true }); }
  }, runtime);
}
