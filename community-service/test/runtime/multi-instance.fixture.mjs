import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { journey, expectStatus, origin } from './journey.fixture.mjs';

export async function exercise(database, runtime) {
  await journey(database, async ({ local, send, provider, admin }) => {
    const control = createServer(async (request, response) => {
      try {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const input = JSON.parse(Buffer.concat(chunks).toString());
        let result;
        if (input.action === 'oauth') {
          provider.subject = input.subject;
          const start = await send(new Request(input.url));
          expectStatus(start, 303);
          const cookie = start.headers['set-cookie'][0].split(';')[0];
          const url = start.headers.location;
          const stateId = new URL(url).searchParams.get('state');
          expectStatus(await send(new Request(url, { headers: { cookie } })));
          expectStatus(await send(new Request(url, { method: 'POST', headers: { cookie, origin } })), 303);
          expectStatus(await send(new Request(`${origin}/v1/discord/callback?state=${stateId}&code=synthetic`, { headers: { cookie } })));
          result = { stateId };
        } else if (input.action === 'state') {
          result = {};
          for (const table of ['contribution_statistics', 'import_receipts', 'revision_votes', 'installation_keys', 'contribution_publications', 'reports']) {
            result[table] = (await database.prepare(`SELECT * FROM ${table}`).all()).results;
          }
          result.payloads = (await database.prepare('SELECT r.revision_id, p.artifact, p.manifest, p.envelope FROM artifact_payloads p JOIN contribution_revisions r ON r.object_key = p.object_key').all()).results
            .map(row => Object.fromEntries(Object.entries(row).map(([name, value]) => [name, name === 'revision_id' ? value : Buffer.from(value).toString('base64')])));
        } else if (input.action === 'membership') {
          assert.ok(['member', 'nonmember', 'outage'].includes(input.mode));
          provider.mode = input.mode;
          await database.prepare('UPDATE discord_membership SET checked_at = 0').run();
          await database.prepare('UPDATE discord_membership_refresh SET retry_at = 0').run();
          result = { mode: provider.mode };
        } else if (input.action === 'moderate') {
          assert.ok(['quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report'].includes(input.command));
          const kind = input.command === 'resolve_report' ? 'report' : input.revisionId ? 'vote' : ['quarantine', 'restore'].includes(input.command) ? 'revision' : 'contributor';
          const snapshot = expectStatus(await admin(`/admin/v1/moderation/${kind}/${input.targetId}${input.revisionId ? '/' + input.revisionId : ''}`));
          result = expectStatus(await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(),
            command: input.command, targetId: input.targetId, expectedRevision: snapshot.revision,
            reason: 'Synthetic multi-instance evidence', ...(input.revisionId ? { revisionId: input.revisionId } : {}) }));
        } else if (input.action === 'storage-failure') {
          if (input.enabled) database.connection.exec(`CREATE TRIGGER synthetic_e2e_failure AFTER INSERT ON artifact_payloads
            BEGIN SELECT RAISE(ABORT, 'synthetic_disk_failure'); END`);
          else database.connection.exec('DROP TRIGGER synthetic_e2e_failure');
          result = { enabled: input.enabled };
        } else throw new Error('unknown_control_action');
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(result));
      } catch (error) {
        response.writeHead(500, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ error: String(error) }));
      }
    });
    control.listen(0, '127.0.0.1');
    await once(control, 'listening');
    const root = runtime.applicationRoot;
    try {
      const child = spawn(process.env.COMMUNITY_E2E_PYTHON ?? join(root, '.venv/bin/python'), ['-m', 'tests.community_e2e'], {
        cwd: root, stdio: ['pipe', 'pipe', 'pipe'], detached: process.platform !== 'win32',
      });
      let output = '', errors = '';
      child.stdout.on('data', chunk => { output += chunk; });
      child.stderr.on('data', chunk => { errors += chunk; });
      const timer = setTimeout(() => {
        if (process.platform === 'win32') child.kill('SIGKILL');
        else process.kill(-child.pid, 'SIGKILL');
      }, 120000);
      child.stdin.end(JSON.stringify({ local, control: `http://127.0.0.1:${control.address().port}` }));
      try {
        const [code] = await once(child, 'exit');
        assert.equal(code, 0, errors + output);
        assert.equal(JSON.parse(output).instances, 3);
        assert.equal(JSON.parse(output).backupRestores, 2);
      } finally { clearTimeout(timer); }
    } finally {
      control.closeAllConnections();
      await new Promise(resolve => control.close(resolve));
    }
  }, runtime);
}