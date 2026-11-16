import { test } from 'node:test';
import { readFileSync, readdirSync, mkdtempSync, rmSync, chmodSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { SqliteDatabase } from '../../src/runtime/sqlite.mjs';
import { migrationsDirectory } from '../../src/runtime/schema.mjs';

test('real Node HTTP publication preserves signatures, idempotence, permission and operator authentication', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-runtime-fixture-'));
  const output = join(directory, 'fixture.mjs');
  await build({ entryPoints: [new URL('./service.fixture.mjs', import.meta.url).pathname], outfile: output,
    bundle: true, platform: 'node', format: 'esm', target: 'node24', mainFields: ['module', 'main'], loader: { '.sql': 'text' },
    plugins: [{ name: 'isolated-node-fixtures', setup(builder) {
      builder.onResolve({ filter: /(?:^|\/)environment(?:\.ts)?$/ }, () => ({ path: 'environment', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const env = {};' }));
    } }] });
  const database = new SqliteDatabase(':memory:');
  try {
    if (process.env.COMMUNITY_RUNTIME_IMAGE) {
      chmodSync(directory, 0o755);
      execFileSync('docker', ['run', '--rm', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--mount', `type=bind,source=${directory},target=/fixture,readonly`, process.env.COMMUNITY_RUNTIME_IMAGE,
        'node', '--input-type=module', '-e', `
          import { readFileSync, readdirSync } from 'node:fs';
          import { SqliteDatabase } from '/app/dist/runtime/sqlite.mjs';
          import { exercise } from '/fixture/fixture.mjs';
          const database = new SqliteDatabase(':memory:');
          try {
            for (const name of readdirSync('/app/migrations').filter(name => name.endsWith('.sql')).sort()) {
              database.connection.exec(readFileSync('/app/migrations/' + name, 'utf8'));
            }
            await exercise(database);
          } finally { database.close(); }`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 30000 });
      return;
    }
    const fixture = await import(pathToFileURL(output).href);
    for (const name of readdirSync(migrationsDirectory).filter(name => name.endsWith('.sql')).sort()) {
      database.connection.exec(readFileSync(new URL(name, migrationsDirectory), 'utf8'));
    }
    await fixture.exercise(database);
  } finally { database.close(); rmSync(directory, { recursive: true, force: true }); }
});