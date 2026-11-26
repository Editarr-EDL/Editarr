import { test } from 'node:test';
import { mkdtempSync, rmSync, chmodSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { SqliteDatabase } from '../../src/runtime/sqlite.mjs';
import { operatorAssets } from '../../src/runtime/http.mjs';
import { initializeDatabase } from '../../src/runtime/schema.mjs';

for (const name of ['identity', 'sharing', 'operator', 'cleanup', ...(process.env.COMMUNITY_RUNTIME_IMAGE ? ['restart'] : ['multi-instance'])]) {
  test(`Node HTTP/SQLite ${name} journey with mocked external providers`, async () => {
    const directory = mkdtempSync(join(tmpdir(), 'community-journey-'));
    const output = join(directory, 'fixture.mjs');
    try {
      await build({ entryPoints: [new URL(`./${name}.fixture.mjs`, import.meta.url).pathname], outfile: output,
        bundle: true, platform: 'node', format: 'esm', target: 'node24', mainFields: ['module', 'main'], loader: { '.sql': 'text' },
        plugins: [{ name: 'isolated-node-fixtures', setup(builder) {
          builder.onResolve({ filter: /(?:^|\/)environment(?:\.ts)?$/ }, () => ({ path: 'environment', namespace: 'fixture' }));
          builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const env = {};' }));
        } }] });
      if (process.env.COMMUNITY_RUNTIME_IMAGE) {
        chmodSync(directory, 0o755);
        execFileSync('docker', ['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
          '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:size=32m',
          '--mount', `type=bind,source=${directory},target=/fixture,readonly`, process.env.COMMUNITY_RUNTIME_IMAGE,
          'node', '--input-type=module', '-e', `
            import { initializeDatabase } from '/app/dist/runtime/schema.mjs';
            import { SqliteDatabase } from '/app/dist/runtime/sqlite.mjs';
            import service, { validateEnvironment } from '/app/dist/runtime/service.mjs';
            import { createHttpServer, operatorAssets } from '/app/dist/runtime/http.mjs';
            import { readConfiguration } from '/app/dist/runtime/environment.mjs';
            import { startMaintenance } from '/app/dist/runtime/maintenance.mjs';
            import { exercise } from '/fixture/fixture.mjs';
            initializeDatabase('/tmp/journey.sqlite');
            const database = new SqliteDatabase('/tmp/journey.sqlite');
            try {
              database.lockWriter();
              await exercise(database, { service, validateEnvironment, createHttpServer, readConfiguration, startMaintenance, serverPath: '/app/dist/runtime/server.mjs', assets: operatorAssets(new URL('file:///app/dist/runtime/assets/')) });
            } finally { database.close(); }`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000 });
      } else {
        initializeDatabase(join(directory, 'journey.sqlite'));
        const database = new SqliteDatabase(join(directory, 'journey.sqlite'));
        try {
          database.lockWriter();
          await (await import(pathToFileURL(output).href)).exercise(database, { assets: operatorAssets(new URL('../../../web/dist-operator/', import.meta.url)),
            applicationRoot: fileURLToPath(new URL('../../../', import.meta.url)) });
        } finally { database.close(); }
      }
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
}
