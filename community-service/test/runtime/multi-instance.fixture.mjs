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
          // Master-set state for the Python journey; raw submission bytes are base64 for byte-preservation checks.
          result = {};
          for (const [name, sql] of [
            ['submissions', `SELECT submission_id, digest, contributor_id, version_id, accepted_seq, tier, state, created_at FROM submissions`],
            ['versions', 'SELECT version_id, work_id, alias_id, label_text, status, merged_into, revision, quarantined FROM versions'],
            ['master_revisions', 'SELECT version_id, revision, snapshot_digest, generated_at FROM master_revisions'],
            ['master_filters', `SELECT filter_id, version_id, category, start_ms, end_ms, confidence, attestations, upvotes, downvotes,
              disputes, coverage_negatives, low_confidence, served FROM master_filters`],
            ['download_receipts', 'SELECT * FROM download_receipts'], ['filter_votes', 'SELECT * FROM filter_votes'],
            ['installation_keys', 'SELECT * FROM installation_keys'], ['reports', 'SELECT * FROM reports'],
          ]) result[name] = (await database.prepare(sql).all()).results;
          result.payloads = (await database.prepare('SELECT submission_id, artifact_bytes, manifest_bytes, envelope_bytes FROM submissions').all()).results
            .map(row => ({ submission_id: row.submission_id, artifact: Buffer.from(row.artifact_bytes).toString('base64'),
              manifest: Buffer.from(row.manifest_bytes).toString('base64'), envelope: Buffer.from(row.envelope_bytes).toString('base64') }));
        } else if (input.action === 'membership') {
          assert.ok(['member', 'nonmember', 'outage'].includes(input.mode));
          provider.mode = input.mode;
          await database.prepare('UPDATE discord_membership SET checked_at = 0').run();
          await database.prepare('UPDATE discord_membership_refresh SET retry_at = 0').run();
          result = { mode: provider.mode };
        } else if (input.action === 'moderate') {
          // input: { command, resource, targetId, contributorId? } as in the §7.8 envelope.
          assert.ok(['quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report'].includes(input.command));
          const key = input.resource === 'vote' ? `${input.targetId}/${input.contributorId}` : encodeURIComponent(input.targetId);
          const snapshot = expectStatus(await admin(`/admin/v1/moderation/${input.resource}/${key}`));
          result = expectStatus(await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(),
            command: input.command, resource: input.resource, targetId: input.targetId, expectedRevision: snapshot.revision,
            reason: 'Synthetic multi-instance evidence', ...(input.contributorId ? { contributorId: input.contributorId } : {}) }));
        } else if (input.action === 'storage-failure') {
          if (input.enabled) database.connection.exec(`CREATE TRIGGER synthetic_e2e_failure AFTER INSERT ON submissions
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