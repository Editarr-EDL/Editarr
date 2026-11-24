import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { accessToken, CliError, operatorClient, operatorOrigin } from './operator/transport.mjs';
import { inspect, list, moderate, page, retry } from './operator/commands.mjs';

const help = `Usage: pnpm operator -- <command> [arguments] --origin https://operator-host
  status
  list <contributors|operations|audit|reports|keys|usage> [--target ID] [--after CURSOR]
  inspect <filter|attestation|submission|contributor|version|vote|report> <ID> [--contributor ID]
  moderate <quarantine|restore|suspend|unsuspend|exclude_vote|restore_vote|resolve_report>
    <filter|attestation|submission|contributor|version|vote|report> <ID>
    --expected-revision N --reason TEXT [--contributor ID] [--confirm --checkpoint NEW_DIRECTORY]
  retry --checkpoint DIRECTORY [--confirm]
  page <contributors|operations|audit|reports|keys> --checkpoint DIRECTORY [--target ID] [--resume]
Mutations default to dry-run. Login separately: cloudflared access login https://operator-host/admin/
Access tokens are retrieved privately through cloudflared, never accepted as arguments.
Votes are addressed by filter ID with --contributor; attestations by submissionId:entryId.
Back up current SQLite stores with Docker Compose's offline maintenance backup command.`;

export async function run(argv, dependencies = {}) {
  if (argv[0] === '--') argv = argv.slice(1);
  if (argv[0] === 'backup') throw new CliError('container_maintenance_required');
  let args;
  try {
    args = parseArgs({ args: argv, allowPositionals: true, options: {
      origin: { type: 'string' }, target: { type: 'string' }, after: { type: 'string' },
      contributor: { type: 'string' }, 'expected-revision': { type: 'string' }, reason: { type: 'string' },
      checkpoint: { type: 'string' }, confirm: { type: 'boolean' }, resume: { type: 'boolean' }, help: { type: 'boolean' },
    } });
  } catch { throw new CliError('invalid_arguments'); }
  if (args.values.help) return { help };
  const [command, first, second, third] = args.positionals;
  const allowed = {
    status: [], list: ['target', 'after'], inspect: ['contributor'],
    moderate: ['expected-revision', 'reason', 'contributor', 'confirm', 'checkpoint'],
    retry: ['checkpoint', 'confirm'], page: ['target', 'checkpoint', 'resume'],
  };
  const counts = { status: 1, list: 2, inspect: 3, moderate: 4, retry: 1, page: 2 };
  if (!allowed[command] || args.positionals.length !== counts[command] ||
      Object.keys(args.values).some(key => key !== 'origin' && !allowed[command].includes(key)))
    throw new CliError('invalid_arguments');
  const origin = operatorOrigin(args.values.origin ?? process.env.EDITARR_OPERATOR_ORIGIN);
  if (['retry', 'page'].includes(command) && !args.values.checkpoint) throw new CliError('checkpoint_required');
  const token = await (dependencies.token ?? accessToken)(origin);
  if (argv.some(value => value.includes(token))) throw new CliError('credentials_in_arguments_denied');
  const request = operatorClient(origin, token, dependencies.fetcher);
  let output;
  if (command === 'status') output = await request('/admin/v1/dashboard/overview');
  if (command === 'list') output = await list(request, first, args.values.target, args.values.after);
  if (command === 'inspect') output = await inspect(request, first, second, args.values.contributor);
  if (command === 'moderate') {
    const expected = args.values['expected-revision'];
    if (!expected || !/^(0|[1-9][0-9]*)$/.test(expected)) throw new CliError('invalid_arguments');
    output = await moderate(request, origin, { command: first, resource: second, target: third, expectedRevision: Number(expected),
      reason: args.values.reason, contributorId: args.values.contributor, confirm: args.values.confirm,
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