import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { accessToken, CliError, operatorClient, operatorOrigin } from './operator/transport.mjs';
import { inspect, list, moderate, page, retry } from './operator/commands.mjs';

const help = `Usage: pnpm operator -- <command> [arguments] --origin https://operator-host
  status
  list <contributions|contributors|operations|audit|reports|keys|usage> [--target ID] [--after CURSOR]
  inspect <revision|contributor|vote|report> <ID> [--revision ID]
  moderate <quarantine|restore|suspend|unsuspend|exclude_vote|restore_vote|resolve_report> <ID>
    --expected-revision N --reason TEXT [--revision ID] [--confirm --checkpoint NEW_DIRECTORY]
  retry --checkpoint DIRECTORY [--confirm]
  page <contributions|contributors|operations|audit|reports|keys> --checkpoint DIRECTORY
    [--target ID] [--resume]
  restore [--confirm] [--resume] --source BACKUP_DIRECTORY --destination NEW_DIRECTORY
    --identity-file PROTECTED_AGE_KEY --maximum-archive-bytes N --maximum-sql-bytes N --artifact-bounds JSON
Mutations default to dry-run. Login separately: cloudflared access login https://operator-host/admin/
Access tokens are retrieved privately through cloudflared, never accepted as arguments.
Back up current SQLite stores with Docker Compose's offline maintenance backup command.
Restore above reads existing legacy archive pairs only; it does not install a Docker database.
Legacy restore is local and disabled, with no provider credential, deployment or activation command.
No live hosted restore jobs or feature-pause commands are enabled.`;

export async function run(argv, dependencies = {}) {
  if (argv[0] === '--') argv = argv.slice(1);
  if (argv[0] === 'backup') throw new CliError('container_maintenance_required');
  let args;
  try {
    args = parseArgs({ args: argv, allowPositionals: true, options: {
      origin: { type: 'string' }, target: { type: 'string' }, after: { type: 'string' },
      revision: { type: 'string' }, 'expected-revision': { type: 'string' }, reason: { type: 'string' },
      checkpoint: { type: 'string' }, confirm: { type: 'boolean' }, resume: { type: 'boolean' }, help: { type: 'boolean' },
      destination: { type: 'string' }, 'maximum-sql-bytes': { type: 'string' }, 'artifact-bounds': { type: 'string' },
      source: { type: 'string' }, 'identity-file': { type: 'string' }, 'maximum-archive-bytes': { type: 'string' },
    } });
  } catch { throw new CliError('invalid_arguments'); }
  if (args.values.help) return { help };
  const [command, first, second] = args.positionals;
  const allowed = {
    status: [], list: ['target', 'after'], inspect: ['revision'],
    moderate: ['expected-revision', 'reason', 'revision', 'confirm', 'checkpoint'],
    retry: ['checkpoint', 'confirm'], page: ['target', 'checkpoint', 'resume'],
    restore: ['source', 'destination', 'identity-file', 'maximum-archive-bytes', 'maximum-sql-bytes',
      'artifact-bounds', 'confirm', 'resume'],
  };
  const counts = { status: 1, list: 2, inspect: 3, moderate: 3, retry: 1, page: 2, restore: 1 };
  if (!allowed[command] || args.positionals.length !== counts[command] ||
      Object.keys(args.values).some(key => key !== 'origin' && !allowed[command].includes(key)))
    throw new CliError('invalid_arguments');
  if (command === 'restore') {
    if (args.values.origin) throw new CliError('invalid_arguments');
    let bounds;
    try { bounds = args.values['artifact-bounds'] ? JSON.parse(args.values['artifact-bounds']) : undefined; }
    catch { throw new CliError('invalid_arguments'); }
    const { restoreJob } = await import('./backup/restore.mjs');
    return restoreJob({ source: args.values.source, destination: args.values.destination,
      identity: args.values['identity-file'], maximum: Number(args.values['maximum-sql-bytes']),
      archiveMaximum: Number(args.values['maximum-archive-bytes']), bounds,
      confirm: args.values.confirm, resume: args.values.resume }, dependencies);
  }
  const origin = operatorOrigin(args.values.origin ?? process.env.EDITARR_OPERATOR_ORIGIN);
  if (['retry', 'page'].includes(command) && !args.values.checkpoint) throw new CliError('checkpoint_required');
  const token = await (dependencies.token ?? accessToken)(origin);
  if (argv.some(value => value.includes(token))) throw new CliError('credentials_in_arguments_denied');
  const request = operatorClient(origin, token, dependencies.fetcher);
  let output;
  if (command === 'status') output = await request('/admin/v1/dashboard/overview');
  if (command === 'list') output = await list(request, first, args.values.target, args.values.after);
  if (command === 'inspect') output = await inspect(request, first, second, args.values.revision);
  if (command === 'moderate') {
    const expected = args.values['expected-revision'];
    if (!expected || !/^(0|[1-9][0-9]*)$/.test(expected)) throw new CliError('invalid_arguments');
    output = await moderate(request, origin, { command: first, target: second, expectedRevision: Number(expected),
      reason: args.values.reason, revisionId: args.values.revision, confirm: args.values.confirm,
      checkpoint: args.values.checkpoint });
  }
  if (command === 'retry') output = await retry(request, origin, args.values.checkpoint, args.values.confirm);
  if (command === 'page') output = await page(request, origin, first, args.values.target,
    args.values.checkpoint, args.values.resume);
  return JSON.parse(JSON.stringify(output).replaceAll(token, '[REDACTED]'));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const output = await run(process.argv.slice(2));
    process.stdout.write(output.help ? `${output.help}\n` : `${JSON.stringify(output)}\n`);
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ error: { code: error instanceof CliError ? error.code : 'operator_cli_failed',
      status: error instanceof CliError ? error.status : 0 } })}\n`);
    process.exitCode = 1;
  }
}