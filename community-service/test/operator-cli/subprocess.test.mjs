import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmod, mkdtemp, readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const fixtures = fileURLToPath(new URL('./fixtures/', import.meta.url));
const entry = fileURLToPath(new URL('../../scripts/operator-cli.mjs', import.meta.url));
async function setup(context) {
  const directory = await mkdtemp(join(tmpdir(), 'css59-process-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  await chmod(join(fixtures, 'cloudflared'), 0o755);
  const calls = join(directory, 'calls.jsonl');
  return { checkpoint: join(directory, 'checkpoint'), calls,
    run: (args, extra = {}) => spawnSync(process.execPath, ['--import', join(fixtures, 'transport.mjs'), entry,
      ...args, '--origin', 'https://operator.example.test'], { encoding: 'utf8',
      env: { ...process.env, PATH: `${fixtures}:${process.env.PATH}`, CSS59_CALLS: calls, ...extra } }) };
}
const action = ['moderate', 'quarantine', 'revision-1', '--expected-revision', '2', '--reason', 'Reviewed evidence'];

test('executable handles help, denies unsupported commands and redacts successful output', async context => {
  const fixture = await setup(context);
  for (const args of [['--help'], ['status']]) {
    const result = fixture.run(args);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(!result.stdout.includes('secret.header.signature'));
  }
  for (const args of [['reset'], ['rotate-key'], ['pause-publishing'], ['backup'], ['status', '--token', 'secret.header.signature']]) {
    const result = fixture.run(args);
    assert.equal(result.status, 1);
    assert.ok(!result.stderr.includes('secret.header.signature'));
  }
});

test('executable suppresses cloudflared errors and denied authorization', async context => {
  const fixture = await setup(context);
  for (const extra of [{ CSS59_AUTH_FAIL: '1' }, { CSS59_DENIED: '1' }]) {
    const result = fixture.run(['status'], extra);
    assert.equal(result.status, 1);
    assert.ok(!`${result.stdout}${result.stderr}`.includes('secret.header.signature'));
    assert.match(result.stderr, /access_login_required|operator_not_allowed/);
  }
});

test('executable dry-run/stale confirmation never POSTs; interrupted retry keeps exact body', async context => {
  const fixture = await setup(context);
  assert.equal(fixture.run(action).status, 0);
  const stale = fixture.run([...action, '--confirm', '--checkpoint', fixture.checkpoint], { CSS59_STALE: '1' });
  assert.equal(stale.status, 1);
  assert.match(stale.stderr, /moderation_conflict/);
  let calls = (await readFile(fixture.calls, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  assert.ok(calls.every(call => call.method === 'GET'));
  const interrupted = fixture.run([...action, '--confirm', '--checkpoint', fixture.checkpoint], { CSS59_INTERRUPTED: '1' });
  assert.equal(interrupted.status, 1);
  const saved = JSON.parse(await readFile(join(fixture.checkpoint, 'state.json'), 'utf8'));
  assert.equal(saved.state, 'pending');
  assert.ok(!JSON.stringify(saved).includes('secret.header.signature'));
  const retry = fixture.run(['retry', '--checkpoint', fixture.checkpoint, '--confirm']);
  assert.equal(retry.status, 0, retry.stderr);
  calls = (await readFile(fixture.calls, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  const posts = calls.filter(call => call.method === 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[0].body, posts[1].body);
});