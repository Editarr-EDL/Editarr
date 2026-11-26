import { appendFile } from 'node:fs/promises';

globalThis.fetch = async (url, options) => {
  const path = new URL(url).pathname;
  await appendFile(process.env.CSS59_CALLS, `${JSON.stringify({ path, method: options.method,
    ...(options.body ? { body: JSON.parse(options.body) } : {}) })}\n`);
  if (process.env.CSS59_DENIED) return Response.json({ error: { code: 'operator_not_allowed' } }, { status: 403 });
  if (path.endsWith('/session')) return Response.json({ version: 1, subject: 'operator-subject' });
  if (path.endsWith('/overview')) return Response.json({ version: 1, databaseSize: { source: 'unavailable', value: null },
    displayName: 'secret.header.signature' });
  if (path === '/admin/v1/moderation') {
    if (process.env.CSS59_INTERRUPTED) throw new Error('secret.header.signature');
    const body = JSON.parse(options.body);
    return Response.json({ version: 1, actionId: body.actionId, resource: `${body.resource}:${body.targetId}`,
      revision: body.expectedRevision + 1, outcome: 'applied' });
  }
  if (path === '/admin/v1/recompute') {
    const body = JSON.parse(options.body);
    return Response.json({ version: 1, jobId: body.actionId, complete: true, nextAfter: null });
  }
  if (path.startsWith('/admin/v1/moderation/version/')) return Response.json({ version: 1, resource: 'version:version-1', revision: 2,
    state: { status: 'candidate' }, audit: [] });
  return Response.json({ version: 1, resource: 'filter:filter-1', revision: process.env.CSS59_STALE ? 3 : 2,
    state: { state: 'published' }, audit: [] });
};