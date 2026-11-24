import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const image = process.env.COMMUNITY_TEST_IMAGE ?? 'editarr-community:local';
const docker = (...arguments_) => execFileSync('docker', arguments_, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000 }).trim();

async function healthy(origin) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try { if ((await fetch(`${origin}/health`, { signal: AbortSignal.timeout(1000) })).ok) return; }
    catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('container_health_timeout');
}

test('native image safely initializes, survives hard restart and verifies an offline backup', async () => {
  const name = `editarr-container-test-${randomUUID()}`;
  const data = `${name}-data`, backups = `${name}-backups`;
  const dataMounts = ['--volume', `${data}:/data`];
  const mounts = [...dataMounts, '--volume', `${backups}:/backups`];
  const hardened = ['--read-only', '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
    '--tmpfs', '/tmp:size=32m,noexec,nosuid,nodev'];
  try {
    const missing = spawnSync('docker', ['run', '--rm', ...hardened, ...mounts, image], { encoding: 'utf8', timeout: 15000 });
    assert.equal(missing.status, 1);
    assert.match(missing.stderr, /community_startup_refused/);
    docker('run', '--rm', ...hardened, ...mounts, image, 'node', 'dist/runtime/manage.mjs', 'init', '--confirm');
    const repeated = spawnSync('docker', ['run', '--rm', ...hardened, ...mounts, image, 'node', 'dist/runtime/manage.mjs', 'init', '--confirm'], { encoding: 'utf8', timeout: 15000 });
    assert.equal(repeated.status, 1);
    docker('run', '--rm', ...hardened, ...mounts, image, 'node', '--input-type=module', '-e', `
      import { DatabaseSync } from 'node:sqlite';
      const database = new DatabaseSync('/data/community.sqlite');
      database.exec("INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at) VALUES ('test', '12345678901234567', 'One Two Three', 'one two three', 1); INSERT INTO installations VALUES ('test-install', 'test', 1); INSERT INTO installation_keys VALUES ('test-key', 'test-install', 'Ed25519', X'00FF0180', 'active', 1, NULL)");
      database.exec("INSERT INTO request_nonces VALUES ('test-key', 'expired-preview', 1, 'test')");
      database.close();`);
    const plan = JSON.parse(docker('run', '--rm', '--network', 'none', ...hardened, '--volume', `${data}:/data:ro`, image,
      'node', 'dist/runtime/manage.mjs', 'retention', '--dry-run'));
    assert.equal(plan.policy.version, 'community-retain-first-1');
    assert.equal(plan.policy.durableRecords, 'indefinite');
    assert.equal(plan.mode, 'read_only');
    assert.equal(plan.schemaVersion, 1);
    assert.equal(plan.payloadBytesReclaimed, 0);
    assert.equal(plan.categories.length, 10);
    assert.equal(plan.categories.find(category => category.category === 'requestNonces').eligible, 1);
    const invalidRetention = spawnSync('docker', ['run', '--rm', '--network', 'none', ...hardened, ...mounts, image,
      'node', 'dist/runtime/manage.mjs', 'retention', '--confirm'], { encoding: 'utf8', timeout: 15000 });
    assert.equal(invalidRetention.status, 1);
    assert.match(invalidRetention.stderr, /retention --dry-run/);
    docker('run', '--detach', '--name', name, ...hardened, ...dataMounts, '--publish', '127.0.0.1::8787', image);
    assert.equal(JSON.parse(docker('inspect', name))[0].Mounts.some(mount => mount.Destination === '/backups'), false);
    const binding = docker('port', name, '8787/tcp');
    assert.match(binding, /^127\.0\.0\.1:\d+$/);
    let origin = `http://${binding}`;
    await healthy(origin);
    assert.equal((await fetch(`${origin}/ready`)).status, 503);
    assert.equal((await fetch(`${origin}/v1/identity`)).status, 503);
    assert.equal((await fetch(`${origin}/admin/`)).status, 503);
    const activePreview = spawnSync('docker', ['run', '--rm', '--network', 'none', ...hardened,
      '--volume', `${data}:/data:ro`, image, 'node', 'dist/runtime/manage.mjs', 'retention', '--dry-run'],
    { encoding: 'utf8', timeout: 15000 });
    assert.equal(activePreview.status, 1);
    assert.equal(activePreview.stdout, '');
    assert.match(activePreview.stderr, /community_management_refused/);
    const duplicate = spawnSync('docker', ['run', '--rm', ...hardened, ...mounts, image], { encoding: 'utf8', timeout: 15000 });
    assert.equal(duplicate.status, 1);
    assert.match(duplicate.stderr, /community_startup_refused/);
    docker('kill', '--signal', 'KILL', name);
    docker('start', name);
    origin = `http://${docker('port', name, '8787/tcp')}`;
    await healthy(origin);
    docker('stop', '--time', '30', name);
    docker('run', '--rm', ...hardened, ...mounts, image, 'node', 'dist/runtime/manage.mjs', 'backup', '--confirm', '--destination', '/backups/verified.sqlite');
    const result = docker('run', '--rm', ...hardened, ...mounts, image, 'node', '--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { DatabaseSync } from 'node:sqlite';
      const restored = new DatabaseSync('/backups/verified.sqlite', { readOnly: true });
      assert.deepEqual(restored.prepare('SELECT public_key FROM installation_keys').get().public_key, new Uint8Array([0,255,1,128]));
      assert.equal(restored.prepare("SELECT COUNT(*) AS count FROM request_nonces WHERE nonce = 'expired-preview'").get().count, 1);
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 1);
      assert.equal(restored.prepare('SELECT lineage FROM schema_lineage').get().lineage, 'community-master-1');
      restored.close(); console.log('snapshot_verified');`);
    assert.equal(result, 'snapshot_verified');
    const inspection = JSON.parse(docker('image', 'inspect', image))[0];
    assert.equal(inspection.Config.User, 'node');
  } finally {
    spawnSync('docker', ['rm', '--force', name], { stdio: 'ignore', timeout: 15000 });
    spawnSync('docker', ['volume', 'rm', data, backups], { stdio: 'ignore', timeout: 15000 });
  }
});

test('real Compose isolates mounts and preserves populated data across image replacement', async () => {
  const project = `editarr-package-test-${randomUUID()}`;
  const root = resolve(fileURLToPath(new URL('../../../', import.meta.url)));
  const fixture = mkdtempSync(resolve(tmpdir(), 'editarr-compose-'));
  const replacementImage = `editarr-community:${project}`;
  const config = resolve(fixture, 'service.json');
  const secrets = resolve(fixture, 'secrets.json');
  writeFileSync(config, JSON.stringify({ enabled: false, trustedTunnel: false, requestBytes: 8192, variables: {} }), { mode: 0o600 });
  writeFileSync(secrets, '{}', { mode: 0o600 });
  const environment = { ...process.env, COMMUNITY_CONFIG_FILE: config, COMMUNITY_SECRETS_FILE: secrets,
    COMMUNITY_PORT: '0', COMMUNITY_IMAGE_TAG: image.replace(/^editarr-community:/, '') };
  const compose = (...arguments_) => execFileSync('docker', ['compose', '--project-name', project,
    '--file', resolve(root, 'community-service/compose.yaml'), '--profile', 'maintenance', ...arguments_],
  { env: environment, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000 }).trim();
  try {
    const specification = JSON.parse(compose('config', '--format', 'json'));
    const api = specification.services.community;
    const maintenance = specification.services.maintenance;
    assert.equal(api.user, '1000:1000');
    assert.equal(api.read_only, true);
    assert.deepEqual(api.cap_drop, ['ALL']);
    assert.ok(api.security_opt.includes('no-new-privileges:true'));
    assert.equal(api.ports[0].host_ip, '127.0.0.1');
    assert.equal(Number(api.cpus), 2);
    assert.equal(api.mem_limit, '2147483648');
    assert.equal(api.pids_limit, 128);
    assert.deepEqual(api.logging.options, { 'max-file': '3', 'max-size': '10m' });
    assert.equal(api.volumes.some(mount => mount.target === '/backups'), false);
    assert.equal(api.volumes.find(mount => mount.target === '/etc/community/service.json').read_only, true);
    assert.equal(maintenance.read_only, true);
    assert.equal(maintenance.user, '1000:1000');
    assert.equal(maintenance.ports, undefined);
    assert.equal(maintenance.secrets, undefined);
    assert.equal(maintenance.environment, undefined);
    assert.deepEqual(maintenance.volumes.map(mount => mount.target).sort(), ['/backups', '/data']);
    assert.notEqual(maintenance.volumes[0].source, maintenance.volumes[1].source);
    assert.equal(specification.secrets.community_service.file, secrets);

    assert.match(compose('run', '--rm', '--no-deps', 'maintenance', 'init', '--confirm'), /community_init_complete/);
    compose('run', '--rm', '--no-deps', '--entrypoint', 'node', 'maintenance', '--input-type=module', '-e', `
      import { DatabaseSync } from 'node:sqlite';
      const database = new DatabaseSync('/data/community.sqlite');
      database.exec("INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at) VALUES ('replacement', '12345678901234567', 'One Two Three', 'one two three', 1); INSERT INTO installations VALUES ('installation', 'replacement', 1); INSERT INTO installation_keys VALUES ('key', 'installation', 'Ed25519', X'00FF0180', 'revoked', 1, 2)");
      database.close();`);
    compose('up', '--detach', '--no-build', 'community');
    let container = compose('ps', '--quiet', 'community');
    const before = JSON.parse(docker('inspect', container))[0];
    assert.equal(before.HostConfig.ReadonlyRootfs, true);
    assert.equal(before.Config.User, '1000:1000');
    assert.equal(before.Mounts.some(mount => mount.Destination === '/backups'), false);
    assert.equal(before.Mounts.find(mount => mount.Destination === '/etc/community/service.json').RW, false);
    assert.equal(before.Mounts.find(mount => mount.Destination === '/run/secrets/community_service').RW, false);
    assert.equal(docker('exec', container, 'node', '-p', 'process.versions.node.split(".")[0]'), '24');
    const migrations = readdirSync(resolve(root, 'community-service/migrations'))
      .filter(name => name.endsWith('.sql')).sort();
    assert.equal(docker('exec', container, 'node', '--input-type=module', '-e', `
      import assert from 'node:assert/strict'; import { readdirSync, existsSync } from 'node:fs';
      assert.deepEqual(readdirSync('migrations').filter(name => name.endsWith('.sql')).sort(), ${JSON.stringify(migrations)});
      assert.ok(existsSync('dist/runtime/assets/index.html')); console.log('packaged_resources_verified');`), 'packaged_resources_verified');
    const initialOrigin = `http://${compose('port', 'community', '8787')}`;
    await healthy(initialOrigin);
    assert.equal((await fetch(`${initialOrigin}/health`)).status, 200);
    for (const route of ['/ready', '/v1/identity', '/admin/', '/v1/discord/callback']) {
      assert.equal((await fetch(`${initialOrigin}${route}`)).status, 503, route);
    }
    compose('stop', 'community');
    assert.match(compose('run', '--rm', '--no-deps', 'maintenance', 'backup', '--confirm', '--destination', '/backups/before-replacement.sqlite'), /community_backup_complete/);
    const replacementDockerfile = resolve(fixture, 'Replacement.Dockerfile');
    writeFileSync(replacementDockerfile, `FROM ${image}\nLABEL editarr.packaging-test=${project}\n`);
    docker('build', '--file', replacementDockerfile, '--tag', replacementImage, fixture);
    environment.COMMUNITY_IMAGE_TAG = project;
    compose('up', '--detach', '--no-build', '--force-recreate', 'community');
    container = compose('ps', '--quiet', 'community');
    assert.notEqual(container, before.Id);
    assert.notEqual(JSON.parse(docker('inspect', container))[0].Image, before.Image);
    const origin = `http://${compose('port', 'community', '8787')}`;
    await healthy(origin);
    assert.equal((await fetch(`${origin}/ready`)).status, 503);
    compose('stop', 'community');
    assert.equal(compose('run', '--rm', '--no-deps', '--entrypoint', 'node', 'maintenance', '--input-type=module', '-e', `
      import assert from 'node:assert/strict'; import { DatabaseSync } from 'node:sqlite';
      for (const path of ['/data/community.sqlite', '/backups/before-replacement.sqlite']) {
        const database = new DatabaseSync(path, { readOnly: true });
        assert.equal(database.prepare('PRAGMA user_version').get().user_version, 1);
        const key = database.prepare('SELECT * FROM installation_keys WHERE key_id = ?').get('key');
        assert.equal(key.state, 'revoked'); assert.equal(key.retired_at, 2);
        assert.deepEqual(key.public_key, new Uint8Array([0,255,1,128]));
        assert.equal(database.prepare('SELECT lineage FROM schema_lineage').get().lineage, 'community-master-1');
        database.close();
      }
      console.log('replacement_preserved');`), 'replacement_preserved');
  } finally {
    try { compose('down'); }
    finally {
      spawnSync('docker', ['volume', 'rm', `${project}_community-data`, `${project}_community-backups`], { stdio: 'ignore', timeout: 15000 });
      spawnSync('docker', ['image', 'rm', replacementImage], { stdio: 'ignore', timeout: 15000 });
      rmSync(fixture, { recursive: true, force: true });
    }
  }
});

test('packaged runtime refuses old-lineage and unknown stores and restores verified baseline snapshots', () => {
  const name = `editarr-upgrade-test-${randomUUID()}`;
  const data = `${name}-data`, backups = `${name}-backups`, restored = `${name}-restored`;
  const hardened = ['--read-only', '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
    '--network', 'none', '--tmpfs', '/tmp:size=32m,noexec,nosuid,nodev'];
  const mounts = ['--volume', `${data}:/data`, '--volume', `${backups}:/backups`];
  try {
    assert.equal(docker('run', '--rm', ...hardened, ...mounts, image, 'node', '--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import { DatabaseSync } from 'node:sqlite';
      import { existsSync, mkdirSync, readFileSync } from 'node:fs';
      import { initializeDatabase, openDatabase, upgradeDatabase, verifiedBackup } from './dist/runtime/schema.mjs';
      mkdirSync('/data/old');
      const old = new DatabaseSync('/data/old/community.sqlite');
      old.exec("CREATE TABLE contributors (contributor_id TEXT PRIMARY KEY, display_name TEXT); CREATE TABLE contribution_revisions (revision_id TEXT); INSERT INTO contributors VALUES ('contributor', 'One'); PRAGMA user_version = 18");
      old.close();
      const before = readFileSync('/data/old/community.sqlite');
      assert.throws(() => openDatabase('/data/old/community.sqlite'), /unsupported_database_lineage/);
      await assert.rejects(upgradeDatabase('/data/old/community.sqlite', '/backups/old.sqlite'), /unsupported_database_lineage/);
      assert.equal(existsSync('/backups/old.sqlite'), false);
      assert.deepEqual(readFileSync('/data/old/community.sqlite'), before);
      initializeDatabase('/data/community.sqlite');
      await assert.rejects(upgradeDatabase('/data/community.sqlite', '/backups/current.sqlite'), /database_already_current/);
      const { database, inventory } = openDatabase('/data/community.sqlite');
      database.connection.exec("INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at) VALUES ('contributor', '12345678901234567', 'One Two Three', 'one two three', 1); INSERT INTO installations VALUES ('installation', 'contributor', 1); INSERT INTO installation_keys VALUES ('key', 'installation', 'Ed25519', X'00FF0180', 'revoked', 1, 2)");
      await verifiedBackup(database, '/backups/baseline.sqlite', inventory);
      database.close();
      const unknown = new DatabaseSync('/data/unknown.sqlite');
      unknown.exec(readFileSync('./migrations/0001_master_baseline.sql', 'utf8') + "; PRAGMA user_version = 1; CREATE TABLE unknown (evidence BLOB)"); unknown.close();
      assert.throws(() => openDatabase('/data/unknown.sqlite'), /unknown_database_schema/);
      console.log('lineage_refusal_verified');`), 'lineage_refusal_verified');
    for (const [path, reason] of [['/data/old/community.sqlite', 'unsupported_database_lineage'], ['/data/unknown.sqlite', 'unknown_database_schema']]) {
      const refusal = spawnSync('docker', ['run', '--rm', ...hardened, ...mounts, '--env', `COMMUNITY_DATABASE=${path}`, image], { encoding: 'utf8', timeout: 15000 });
      assert.equal(refusal.status, 1);
      assert.match(refusal.stderr, new RegExp(`community_startup_refused reason=${reason}`));
    }
    assert.equal(docker('run', '--rm', ...hardened, '--volume', `${restored}:/data`, '--volume', `${backups}:/backups:ro`, image, 'node', '--input-type=module', '-e', `
      import assert from 'node:assert/strict'; import { copyFileSync, chmodSync, constants } from 'node:fs';
      import { openDatabase } from './dist/runtime/schema.mjs';
      copyFileSync('/backups/baseline.sqlite', '/data/community.sqlite', constants.COPYFILE_EXCL);
      chmodSync('/data/community.sqlite', 0o600);
      const { database } = openDatabase('/data/community.sqlite');
      assert.equal(database.connection.prepare('SELECT state FROM installation_keys').get().state, 'revoked');
      assert.deepEqual(database.connection.prepare('SELECT public_key FROM installation_keys').get().public_key, new Uint8Array([0,255,1,128]));
      database.close();
      console.log('isolated_restore_verified');`), 'isolated_restore_verified');
  } finally {
    spawnSync('docker', ['volume', 'rm', data, backups, restored], { stdio: 'ignore', timeout: 15000 });
  }
});

test('Dockerfile-specific context allowlist excludes scanner, media and private state', () => {
  const name = `editarr-context-test-${randomUUID()}`;
  const root = resolve(fileURLToPath(new URL('../../../', import.meta.url)));
  const fixture = mkdtempSync(resolve(tmpdir(), 'editarr-context-'));
  const dockerfile = resolve(fixture, 'Dockerfile');
  writeFileSync(dockerfile, 'FROM scratch\nCOPY . /context\n');
  writeFileSync(`${dockerfile}.dockerignore`, readFileSync(resolve(root, 'community-service/Dockerfile.dockerignore')));
  const included = ['community-service/package.json', 'community-service/src/runtime/schema.mjs',
    'community-service/migrations/0001_master_baseline.sql', 'web/operator/index.html',
    'web/src/settings/community-operator/runtime.tsx', 'web/public/favicon-32.png'];
  const excluded = ['scanner/api.py', 'native/fixture', 'data/community.sqlite', 'data/secrets/fixture.key',
    'input_images/fixture.png', 'media/fixture.mkv', '.env', 'community-service/container/service.json',
    'community-service/test/fixture.mjs', 'web/node_modules/fixture.js', 'web/dist-operator/index.html',
    'web/public/private.png', 'web/src/service.json', 'community-service/src/.env.local'];
  for (const name of [...included, ...excluded]) {
    const path = resolve(fixture, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, 'public synthetic context sentinel');
  }
  try {
    docker('build', '--file', dockerfile, '--tag', name, fixture);
    docker('create', '--name', name, name, '/not-executed');
    const archive = execFileSync('docker', ['export', name], { timeout: 60000, maxBuffer: 32 * 1024 * 1024 });
    const files = execFileSync('tar', ['-tf', '-'], { input: archive, encoding: 'utf8', timeout: 15000 })
      .split('\n').map(name => name.replace(/^\.\//, '')).filter(Boolean);
    for (const name of included) assert.ok(files.includes('context/' + name), name);
    for (const name of excluded) assert.equal(files.includes('context/' + name), false, name);
    const permitted = ['community-service', 'web'];
    assert.equal(files.some(name => name.startsWith('context/') && name.split('/')[1] && !permitted.includes(name.split('/')[1])), false);
    assert.equal(files.some(name => /\/(node_modules|data|secrets|models|input_images|dist|dist-operator|test|tests)\//.test(name) ||
      /\/(\.env[^/]*|[^/]*\.(sqlite|db|key|mkv|mp4))$/.test(name) || name.endsWith('/service.json')), false);
  } finally {
    spawnSync('docker', ['rm', '--force', name], { stdio: 'ignore', timeout: 15000 });
    spawnSync('docker', ['image', 'rm', name], { stdio: 'ignore', timeout: 15000 });
    rmSync(fixture, { recursive: true, force: true });
  }
});
