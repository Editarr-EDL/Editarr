import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { list, moderate, recompute, retry, page } from '../../scripts/operator/commands.mjs';
import { CliError } from '../../scripts/operator/transport.mjs';
import { transferPage } from '../../scripts/operator/transfer.mjs';
import { withCheckpoint } from '../../scripts/operator/state.mjs';

const origin = 'https://operator.example.test';
const options = { command: 'quarantine', resource: 'filter', target: 'filter-1', expectedRevision: 2, reason: 'Reviewed evidence' };
const snapshot = { version: 1, resource: 'filter:filter-1', revision: 2, state: { state: 'active' }, audit: [] };
const identity = { version: 1, subject: 'operator-subject' };
async function temporary(context) {
  const directory = await mkdtemp(join(tmpdir(), 'css59-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  return join(directory, 'checkpoint');
}
function fixture(send) {
  return async (path, body) => {
    if (path === '/admin/v1/session') return identity;
    if (path === '/admin/v1/moderation') return send(body);
    return snapshot;
  };
}

test('dry-run and stale inspection cannot mutate', async () => {
  let sends = 0;
  const request = fixture(() => { sends++; });
  assert.equal((await moderate(request, origin, options)).dryRun, true);
  await assert.rejects(moderate(request, origin, { ...options, expectedRevision: 1, confirm: true }), /moderation_conflict/);
  await assert.rejects(moderate(request, origin, { ...options, confirm: true }), /checkpoint_required/);
  assert.equal(sends, 0);
});

test('uncertain moderation persists private exact intent; retry applies once and re-reads current state', async context => {
  const checkpoint = await temporary(context);
  let initial;
  await assert.rejects(moderate(fixture(body => { initial = body; throw new CliError('operator_transport_unavailable'); }),
    origin, { ...options, confirm: true, checkpoint }), /operator_transport_unavailable/);
  const saved = JSON.parse(await readFile(join(checkpoint, 'state.json'), 'utf8'));
  assert.deepEqual(saved.body, initial);
  assert.equal(saved.state, 'pending');
  assert.equal((await stat(checkpoint)).mode & 0o077, 0);
  assert.equal((await stat(join(checkpoint, 'state.json'))).mode & 0o077, 0);
  assert.equal((await retry(fixture(() => assert.fail('dry-run')), origin, checkpoint, false)).dryRun, true);
  const result = await retry(fixture(body => {
    assert.deepEqual(body, initial);
    return { version: 1, actionId: body.actionId, resource: snapshot.resource, revision: 3, outcome: 'applied' };
  }), origin, checkpoint, true);
  assert.equal(result.result.revision, 3);
  assert.equal(result.current.revision, 2);
  await retry(fixture(() => assert.fail('acknowledged action must not resend')), origin, checkpoint, true);
});

test('conflicts stop retry and wrong human or origin cannot use a journal', async context => {
  const checkpoint = await temporary(context);
  const request = fixture(() => { throw new CliError('moderation_conflict', 409); });
  await assert.rejects(moderate(request, origin, { ...options, confirm: true, checkpoint }), /moderation_conflict/);
  await assert.rejects(retry(request, origin, checkpoint, true), /action_requires_fresh_review/);
  await assert.rejects(retry(request, 'https://other.example.test', checkpoint, true), /checkpoint_identity_mismatch/);
  await assert.rejects(retry(async () => ({ ...identity, subject: 'other' }), origin, checkpoint, true), /checkpoint_identity_mismatch/);
});

test('invalid success remains pending, preserving exact ID', async context => {
  const checkpoint = await temporary(context);
  await assert.rejects(moderate(fixture(body => ({ version: 1, actionId: body.actionId, resource: 'filter:other',
    revision: 3, outcome: 'applied' })), origin, { ...options, confirm: true, checkpoint }), /invalid_operator_data/);
  assert.equal(JSON.parse(await readFile(join(checkpoint, 'state.json'), 'utf8')).state, 'pending');
});

test('paginated reads resume exact cursor and preserve durable pages', async context => {
  const checkpoint = await temporary(context);
  let reads = 0;
  const request = async path => {
    if (path === '/admin/v1/session') return identity;
    reads++;
    assert.equal(path, reads === 1 ? '/admin/v1/dashboard/operations' : '/admin/v1/dashboard/operations?after=YQ%3D%3D');
    return { version: 1, observedAt: 1, source: 'service_database', items: [{ operationId: `operation-${reads}` }],
      nextCursor: reads === 1 ? 'YQ==' : null };
  };
  const first = await page(request, origin, 'operations', undefined, checkpoint, false);
  assert.equal(first.pages, 1);
  assert.equal(first.complete, false);
  await assert.rejects(page(request, origin, 'audit', undefined, checkpoint, true), /checkpoint_scope_mismatch/);
  assert.equal((await page(request, origin, 'operations', undefined, checkpoint, true)).complete, true);
  assert.equal((await page(request, origin, 'operations', undefined, checkpoint, true)).pages, 2);
  assert.equal(reads, 2);
  assert.equal(JSON.parse(await readFile(join(checkpoint, 'page-000000.json'), 'utf8')).data.items[0].operationId, 'operation-1');
});

test('transfer adapters retain exact BLOB bytes and advance only after destination commit', async () => {
  const bytes = new Uint8Array([0, 255, 128, 13, 10]);
  const read = async cursor => ({ cursor, nextCursor: 'next', data: { schemaRevision: '0014', bytes } });
  await assert.rejects(transferPage(null, read, async value => {
    assert.strictEqual(value.data.bytes, bytes);
    throw new Error('interrupted');
  }), /interrupted/);
  assert.deepEqual(await transferPage(null, read, async value => {
    assert.deepEqual(value.data.bytes, bytes);
    assert.equal(value.data.schemaRevision, '0014');
  }), { nextCursor: 'next', complete: false });
  await assert.rejects(transferPage('next', read, async () => assert.fail('must not commit')), /invalid_transfer_page/);
});

test('all approved commands bind the same resource/token/reason and reject mismatched vote identity', async () => {
  const cases = [['quarantine', 'filter'], ['restore', 'version'], ['quarantine', 'attestation'], ['restore', 'submission'],
    ['quarantine', 'contributor'], ['suspend', 'contributor'], ['unsuspend', 'contributor'], ['exclude_vote', 'vote'],
    ['restore_vote', 'vote'], ['resolve_report', 'report']];
  for (const [command, kind] of cases) {
    const target = kind === 'attestation' ? 'submission-1:entry-1' : 'target-1';
    const contributorId = kind === 'vote' ? 'contributor-2' : undefined;
    const request = async path => {
      if (path.includes('/dashboard/')) return { version: 1, observedAt: 1, source: 'service_database', items: [], nextCursor: 'next' };
      return { ...snapshot, resource: `${kind}:${target}${contributorId ? `:${contributorId}` : ''}` };
    };
    const preview = await moderate(request, origin, { ...options, command, resource: kind, target, contributorId });
    assert.equal(preview.action.command, command);
    assert.equal(preview.action.resource, kind);
    assert.equal(preview.action.expectedRevision, 2);
    if (kind === 'contributor') assert.match(preview.scope, /all_installations/);
  }
  for (const invalid of [{ contributorId: 'extra' }, { command: 'suspend' }, { resource: 'vote' }, { resource: 'attestation' }])
    await assert.rejects(moderate(fixture(() => assert.fail()), origin, { ...options, ...invalid }), /invalid_operator_data/);
});

test('auth loss, pressure and post-commit budget failures preserve pending exact intent', async context => {
  const base = await temporary(context);
  for (const [status, code] of [[401, 'operator_token_invalid'], [403, 'operator_not_allowed'],
    [413, 'moderation_query_limit'], [429, 'operator_rate_limited'], [503, 'service_unavailable']]) {
    const checkpoint = `${base}-${status}`;
    await assert.rejects(moderate(fixture(() => { throw new CliError(code, status); }), origin,
      { ...options, confirm: true, checkpoint }), error => error.code === code);
    assert.equal(JSON.parse(await readFile(join(checkpoint, 'state.json'), 'utf8')).state, 'pending');
  }
});

test('crash between page commit and cursor update resumes stored data without refetching', async context => {
  const checkpoint = await temporary(context);
  const path = '/admin/v1/dashboard/operations';
  const data = { version: 1, observedAt: 1, source: 'service_database', items: [{ operationId: 'durable' }], nextCursor: 'next' };
  await withCheckpoint(checkpoint, true, async store => {
    await store.write('state.json', { version: 1, origin, subject: identity.subject, path, nextCursor: null, complete: false, pages: 0 });
    await store.write('page-000000.json', { cursor: null, nextCursor: 'next', data });
  });
  const result = await page(async requestPath => {
    assert.equal(requestPath, '/admin/v1/session');
    return identity;
  }, origin, 'operations', undefined, checkpoint, true);
  assert.equal(result.pages, 1);
  assert.deepEqual(result.page, data);
});

test('unsafe, symlinked or locked checkpoints fail closed; no reset shortcut is used', async context => {
  const checkpoint = await temporary(context);
  await mkdir(checkpoint, { mode: 0o700 });
  await chmod(checkpoint, 0o755);
  await assert.rejects(withCheckpoint(checkpoint, false, () => assert.fail()), /unsafe_checkpoint/);
  await chmod(checkpoint, 0o700);
  const linked = `${checkpoint}-linked`;
  await symlink(checkpoint, linked);
  await assert.rejects(withCheckpoint(linked, false, () => assert.fail()), /unsafe_checkpoint/);
  await writeFile(join(checkpoint, 'lock'), '{}', { mode: 0o600 });
  await assert.rejects(withCheckpoint(checkpoint, false, () => assert.fail()), /checkpoint_exists_or_locked/);
});
test('catalog actions bind their command-specific fields and reject incomplete or mixed extras', async () => {
  const cases = [[{ command: 'confirm_version', resource: 'version' }, {}],
    [{ command: 'merge_version', resource: 'version', intoVersionId: 'version-2' }, { intoVersionId: 'version-2' }],
    [{ command: 'approve_alias', resource: 'alias_suggestion', aliasId: 'theatrical' }, { aliasId: 'theatrical' }],
    [{ command: 'approve_alias', resource: 'alias_suggestion', newAliasId: 'remastered', label: 'Remastered' },
      { newAliasId: 'remastered', label: 'Remastered' }],
    [{ command: 'reject_alias', resource: 'alias_suggestion' }, {}]];
  for (const [input, fields] of cases) {
    const request = async path => {
      assert.equal(path, `/admin/v1/moderation/${input.resource}/target-1`);
      return { ...snapshot, resource: `${input.resource}:target-1` };
    };
    const preview = await moderate(request, origin, { ...options, ...input, target: 'target-1' });
    assert.deepEqual({ ...preview.action, actionId: 'id' }, { version: 1, actionId: 'id', command: input.command, resource: input.resource,
      targetId: 'target-1', expectedRevision: 2, reason: options.reason, ...fields });
  }
  for (const invalid of [{ command: 'merge_version', resource: 'version' },
    { command: 'merge_version', resource: 'version', intoVersionId: 'target-1' },
    { command: 'approve_alias', resource: 'alias_suggestion' },
    { command: 'approve_alias', resource: 'alias_suggestion', aliasId: 'theatrical', newAliasId: 'other', label: 'Other' },
    { command: 'approve_alias', resource: 'alias_suggestion', newAliasId: 'remastered' },
    { command: 'approve_alias', resource: 'alias_suggestion', aliasId: 'Not Valid' },
    { command: 'confirm_version', resource: 'version', intoVersionId: 'version-2' },
    { command: 'confirm_version', resource: 'alias_suggestion' }, { intoVersionId: 'version-2' }])
    await assert.rejects(moderate(fixture(() => assert.fail()), origin, { ...options, target: 'target-1', ...invalid }), /invalid_operator_data/);
});

test('recompute is dry-run by default and journals one exact action for each confirmed batch', async context => {
  const checkpoint = await temporary(context);
  const preview = await recompute(async () => assert.fail('dry-run must not request'), origin, { versionIds: ['v1', 'v2'], reason: 'Policy changed' });
  assert.equal(preview.dryRun, true);
  assert.deepEqual(preview.action.versionIds, ['v1', 'v2']);
  for (const invalid of [{ versionIds: ['all', 'v1'] }, { versionIds: ['v1', 'v1'] }, { versionIds: ['v1'], after: 'v0' },
    { versionIds: ['../x'] }, { versionIds: ['all'], reason: ' ' }])
    await assert.rejects(recompute(async () => assert.fail(), origin, { reason: 'r', ...invalid }), /invalid_arguments|invalid_operator_data/);
  await assert.rejects(recompute(async () => assert.fail(), origin, { versionIds: ['all'], reason: 'r', confirm: true }), /checkpoint_required/);
  let sent;
  const request = async (path, body) => {
    if (path === '/admin/v1/session') return identity;
    assert.equal(path, '/admin/v1/recompute');
    if (!sent) { sent = body; throw new CliError('operator_transport_unavailable'); }
    assert.deepEqual(body, sent);
    return { version: 1, jobId: body.actionId, complete: false, nextAfter: 'v200' };
  };
  await assert.rejects(recompute(request, origin, { versionIds: ['all'], after: 'v0', reason: 'Policy changed', confirm: true, checkpoint }),
    /operator_transport_unavailable/);
  assert.deepEqual(sent, { version: 1, actionId: sent.actionId, versionIds: 'all', after: 'v0', reason: 'Policy changed' });
  const result = await retry(request, origin, checkpoint, true);
  assert.equal(result.result.nextAfter, 'v200');
  assert.deepEqual(await retry(async path => path === '/admin/v1/session' ? identity : assert.fail('applied batch must not resend'),
    origin, checkpoint, true), result);
  const listed = await temporary(context);
  await assert.rejects(recompute(async (path, body) => path === '/admin/v1/session' ? identity :
    { version: 1, actionId: body.actionId, versions: [] }, origin, { versionIds: ['v1'], reason: 'r', confirm: true, checkpoint: listed }),
  /invalid_operator_data/);
});

test('version and alias suggestion lists pass their filter and validate their keyed pages', async () => {
  const paths = [];
  const request = async path => {
    paths.push(path);
    return path.startsWith('/admin/v1/versions') ? { version: 1, versions: [{ versionId: 'v1' }], nextCursor: null }
      : { version: 1, suggestions: [], nextCursor: null };
  };
  assert.equal((await list(request, 'versions', undefined, 'v0', 'candidate')).versions.length, 1);
  await list(request, 'alias-suggestions', undefined, undefined, 'pending');
  assert.deepEqual(paths, ['/admin/v1/versions?status=candidate&after=v0', '/admin/v1/alias-suggestions?state=pending']);
  await assert.rejects(list(request, 'versions', undefined, undefined, 'pending'), /invalid_arguments/);
  await assert.rejects(list(request, 'contributors', undefined, undefined, 'active'), /invalid_arguments/);
  await assert.rejects(list(async () => ({ version: 1, items: [], nextCursor: null }), 'versions'), /invalid_operator_data/);
});
