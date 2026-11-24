import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import { CliError } from './transport.mjs';
import { withCheckpoint } from './state.mjs';
import { transferPage } from './transfer.mjs';

const id = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
const revision = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER - 1);
const cursor = z.string().min(1).max(1024).nullable();
const commands = ['quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report'];
const resources = ['filter', 'attestation', 'submission', 'contributor', 'version', 'vote', 'report'];
const allowed = { quarantine: ['filter', 'attestation', 'submission', 'contributor', 'version'],
  restore: ['filter', 'attestation', 'submission', 'contributor', 'version'], suspend: ['contributor'],
  unsuspend: ['contributor'], exclude_vote: ['vote'], restore_vote: ['vote'], resolve_report: ['report'] };
const attestation = z.string().regex(/^[A-Za-z0-9_.-]{1,128}:[A-Za-z0-9_.-]{1,32}$/);
const action = z.strictObject({ version: z.literal(1), actionId: z.string().uuid(), command: z.enum(commands),
  resource: z.enum(resources), targetId: z.string().max(161), contributorId: id.optional(), expectedRevision: revision,
  reason: z.string().max(1000).refine(value => value.trim().length > 0 && !/[\x00-\x1f\x7f]/.test(value)),
}).refine(value => allowed[value.command].includes(value.resource) && (value.resource === 'vote') === (value.contributorId !== undefined) &&
  (value.resource === 'attestation' ? attestation : id).safeParse(value.targetId).success);
const inspection = z.strictObject({ version: z.literal(1), resource: z.string().max(400), revision,
  state: z.record(z.string(), z.unknown()), audit: z.array(z.record(z.string(), z.unknown())).max(20) });
const session = z.strictObject({ version: z.literal(1), subject: z.string().min(1).max(256) });
const actionJournal = z.strictObject({ version: z.literal(1), origin: z.string(), subject: session.shape.subject,
  body: action, state: z.enum(['pending', 'applied', 'terminal']), error: z.string().regex(/^[a-z_]{1,80}$/).optional() });
const pageJournal = z.strictObject({ version: z.literal(1), origin: z.string(), subject: session.shape.subject,
  path: z.string(), nextCursor: cursor, complete: z.boolean(), pages: z.number().int().min(0).max(1000000) });

function parse(schema, value) {
  const result = schema.safeParse(value);
  if (!result.success) throw new CliError('invalid_operator_data');
  return result.data;
}
function resource(body) {
  return body.resource === 'vote' ? `vote:${body.targetId}:${body.contributorId}` : `${body.resource}:${body.targetId}`;
}
function inspectPath(kind, target, contributor) {
  if (!resources.includes(kind) || (kind === 'vote') !== (contributor !== undefined)) throw new CliError('invalid_resource');
  parse(kind === 'attestation' ? attestation : id, target);
  if (contributor !== undefined) parse(id, contributor);
  return `/admin/v1/moderation/${kind}/${encodeURIComponent(target)}${contributor ? `/${contributor}` : ''}`;
}
function listPath(collection, target) {
  if (collection === 'reports' && target === undefined) return '/admin/v1/reports';
  if (['keys', 'usage'].includes(collection)) {
    return `/admin/v1/dashboard/contributors/${parse(id, target)}/${collection}`;
  }
  if (!['contributors', 'operations', 'audit'].includes(collection) || target !== undefined)
    throw new CliError('invalid_collection');
  return `/admin/v1/dashboard/${collection}`;
}
function validatePage(value, path) {
  const rows = z.array(z.record(z.string(), z.unknown())).max(20);
  return parse(path === '/admin/v1/reports'
    ? z.strictObject({ version: z.literal(1), reports: rows, nextCursor: cursor })
    : z.strictObject({ version: z.literal(1), observedAt: z.number().int().min(0),
      source: z.literal('service_database'), items: rows, nextCursor: cursor }), value);
}
function boundJournal(value, origin, subject) {
  if (value.origin !== origin || value.subject !== subject) throw new CliError('checkpoint_identity_mismatch');
  return value;
}
export async function inspect(request, kind, target, contributor) {
  const value = parse(inspection, await request(inspectPath(kind, target, contributor)));
  if (value.resource !== `${kind}:${target}${contributor ? `:${contributor}` : ''}`)
    throw new CliError('invalid_operator_data');
  return value;
}
export async function list(request, collection, target, after) {
  const path = listPath(collection, target);
  if (after !== undefined && (typeof after !== 'string' || !after || after.length > 1024)) throw new CliError('invalid_cursor');
  if (collection === 'usage') {
    if (after !== undefined) throw new CliError('invalid_cursor');
    return request(path);
  }
  return validatePage(await request(`${path}${after === undefined ? '' : `?after=${encodeURIComponent(after)}`}`), path);
}

async function submit(request, store, saved) {
  if (saved.state === 'terminal') throw new CliError('action_requires_fresh_review');
  if (saved.state === 'pending') {
    let result;
    try {
      result = parse(z.strictObject({ version: z.literal(1), actionId: z.literal(saved.body.actionId),
        resource: z.literal(resource(saved.body)), revision: z.literal(saved.body.expectedRevision + 1),
        outcome: z.literal('applied') }), await request('/admin/v1/moderation', saved.body));
    } catch (error) {
      const terminal = [400, 404, 409].includes(error.status) || error.code === 'moderation_ineligible';
      await store.write('state.json', { ...saved, state: terminal ? 'terminal' : 'pending',
        error: error instanceof CliError ? error.code : 'operator_transport_unavailable' });
      throw error;
    }
    saved = { ...saved, state: 'applied' };
    delete saved.error;
    await store.write('state.json', saved);
    return { result, current: await currentState(request, saved.body) };
  }
  return { result: { version: 1, actionId: saved.body.actionId, resource: resource(saved.body),
    revision: saved.body.expectedRevision + 1, outcome: 'applied' }, current: await currentState(request, saved.body) };
}
async function currentState(request, body) {
  try { return await inspect(request, body.resource, body.targetId, body.contributorId); }
  catch { return { available: false }; }
}

export async function moderate(request, origin, options) {
  const body = parse(action, { version: 1, actionId: randomUUID(), command: options.command, resource: options.resource,
    targetId: options.target, expectedRevision: options.expectedRevision, reason: options.reason,
    ...(options.contributorId === undefined ? {} : { contributorId: options.contributorId }) });
  const kind = body.resource;
  const fresh = await inspect(request, kind, body.targetId, body.contributorId);
  if (fresh.revision !== body.expectedRevision) throw new CliError('moderation_conflict', 409);
  const preview = { dryRun: !options.confirm, action: body, current: fresh,
    ...(kind === 'contributor' ? { credentials: await list(request, 'keys', body.targetId),
      scope: 'all_installations_including_keys_beyond_this_page; local_downloads_are_not_recalled' } : {}) };
  if (!options.confirm) return preview;
  if (!options.checkpoint) throw new CliError('checkpoint_required');
  const identity = parse(session, await request('/admin/v1/session'));
  return withCheckpoint(options.checkpoint, true, async store => {
    const saved = { version: 1, origin, subject: identity.subject, body, state: 'pending' };
    await store.write('state.json', saved);
    return submit(request, store, saved);
  });
}

export async function retry(request, origin, checkpoint, confirm) {
  const identity = parse(session, await request('/admin/v1/session'));
  return withCheckpoint(checkpoint, false, async store => {
    const saved = boundJournal(parse(actionJournal, await store.read('state.json')), origin, identity.subject);
    if (!confirm) return { dryRun: true, ...saved };
    return submit(request, store, saved);
  });
}

export async function page(request, origin, collection, target, checkpoint, resume) {
  const path = listPath(collection, target);
  if (collection === 'usage') throw new CliError('invalid_collection');
  const identity = parse(session, await request('/admin/v1/session'));
  return withCheckpoint(checkpoint, !resume, async store => {
    const saved = resume ? boundJournal(parse(pageJournal, await store.read('state.json')), origin, identity.subject)
      : { version: 1, origin, subject: identity.subject, path, nextCursor: null, complete: false, pages: 0 };
    if (saved.path !== path) throw new CliError('checkpoint_scope_mismatch');
    if (saved.complete) return saved;
    if (!resume) await store.write('state.json', saved);
    const name = `page-${String(saved.pages).padStart(6, '0')}.json`;
    const next = await transferPage(saved.nextCursor, async after => {
      const stored = await store.read(name);
      if (stored) {
        const valid = parse(z.strictObject({ cursor, nextCursor: cursor, data: z.unknown() }), stored);
        validatePage(valid.data, path);
        if (valid.nextCursor !== valid.data.nextCursor) throw new CliError('invalid_checkpoint');
        return valid;
      }
      const data = await list(request, collection, target, after ?? undefined);
      return { cursor: after, nextCursor: data.nextCursor, data };
    }, value => store.write(name, value));
    const state = { ...saved, ...next, pages: saved.pages + 1 };
    await store.write('state.json', state);
    return { ...state, page: (await store.read(name)).data };
  });
}