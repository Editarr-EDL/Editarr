import assert from 'node:assert/strict';
import { mkdtempSync, copyFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer, request as httpRequest } from 'node:http';
import { journey, expectStatus, patch, timestamp } from './journey.fixture.mjs';
import { preparedRotation, signedRead } from '../helpers/key-lifecycle.ts';

async function availablePort() {
  const server = createServer(); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}

async function start(serverPath, directory, port) {
  const child = spawn(process.execPath, [serverPath], { env: { ...process.env,
    COMMUNITY_CONFIG_FILE: join(directory, 'config.json'), COMMUNITY_SECRETS_FILE: join(directory, 'secrets.json'),
    COMMUNITY_DATABASE: join(directory, 'restart.sqlite'), HOST: '127.0.0.1', PORT: String(port),
  }, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '';
  child.stderr.on('data', chunk => { logs += chunk; });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('startup_timeout')); }, 15000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`startup_exit_${code}: ${logs}`)); });
    child.stdout.on('data', chunk => {
      if (String(chunk).includes('community_listening')) { clearTimeout(timer); resolve(); }
    });
  });
  return child;
}

async function stop(child, signal) {
  const closed = once(child, 'exit'); child.kill(signal);
  const timer = setTimeout(() => child.kill('SIGKILL'), 10000);
  try {
    const [code, actualSignal] = await closed;
    if (signal === 'SIGTERM') assert.equal(code, 0);
    else assert.equal(actualSignal, signal);
  } finally { clearTimeout(timer); }
}

function send(port, request, discardResponse = false) {
  return new Promise(async (resolve, reject) => {
    try {
      const url = new URL(request.url), body = Buffer.from(await request.arrayBuffer());
      const headers = { ...Object.fromEntries(request.headers), host: url.host, 'cf-connecting-ip': '192.0.2.250' };
      if (body.length) headers['content-length'] = body.length;
      const outgoing = httpRequest(`http://127.0.0.1:${port}${url.pathname}${url.search}`, { method: request.method, headers }, response => {
        if (discardResponse) { response.destroy(); resolve(); return; }
        const chunks = []; response.on('data', chunk => chunks.push(chunk));
        response.on('end', () => { const text = Buffer.concat(chunks).toString(); resolve({ status: response.statusCode, text, json: JSON.parse(text) }); });
      });
      outgoing.on('error', reject); outgoing.end(body);
    } catch (error) { reject(error); }
  });
}

export async function exercise(database, runtime) {
  const directory = mkdtempSync(join(tmpdir(), 'community-restart-'));
  let alice, bob, retired, revoked, replay, nonce, child;
  try {
    await journey(database, async ({ enroll, post, send: localSend, admin, configuration }) => {
      alice = await enroll('1554889314549243999'); bob = await enroll('1554889314549243988');
      retired = await enroll(alice.subject); revoked = await enroll(alice.subject);
      const rotation = await preparedRotation(retired);
      expectStatus(await post(retired, '/v1/keys/rotate', rotation.body, rotation.operationId));
      expectStatus(await post(alice, '/v1/keys/revoke', {
        version: 1, installationId: revoked.installationId, keyId: revoked.keyId,
      }));
      nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64url');
      replay = await patch(alice, { displayName: 'Restart Preserved Name' }, crypto.randomUUID(), nonce);
      expectStatus(await localSend(replay.clone()));
      const inspected = expectStatus(await admin(`/admin/v1/moderation/contributor/${bob.contributorId}`));
      expectStatus(await admin('/admin/v1/moderation', { version: 1, command: 'suspend', actionId: crypto.randomUUID(),
        targetId: bob.contributorId, expectedRevision: inspected.revision, reason: 'Synthetic persistent moderation' }));
      copyFileSync(configuration.configPath, join(directory, 'config.json'));
      copyFileSync(configuration.secretsPath, join(directory, 'secrets.json'));
    }, runtime);
    // Copy a populated, known-current fixture into a separate file for the actual server process.
    database.connection.prepare('VACUUM INTO ?').run(join(directory, 'restart.sqlite'));
    const port = await availablePort();
    const read = key => signedRead(key, '/v1/identity', timestamp()).then(request => send(port, request));
    child = await start(runtime.serverPath, directory, port);
    assert.equal(expectStatus(await read(alice)).displayName, 'Restart Preserved Name');
    expectStatus(await read(bob), 401);
    expectStatus(await read(retired), 401); expectStatus(await read(revoked), 401);
    expectStatus(await send(port, replay.clone()));
    expectStatus(await send(port, await patch(alice, { displayName: 'Nonce Must Not Replay' }, crypto.randomUUID(), nonce)), 409);
    const interruptedOperation = crypto.randomUUID();
    const interruptedBody = { displayName: 'Recovered Mutation Name' };
    const interrupted = await patch(alice, interruptedBody, interruptedOperation);
    await send(port, interrupted.clone(), true);
    await stop(child, 'SIGKILL'); child = undefined;
    child = await start(runtime.serverPath, directory, port);
    assert.equal(expectStatus(await read(alice)).displayName, interruptedBody.displayName);
    const recovered = expectStatus(await send(port, await patch(alice, interruptedBody, interruptedOperation)));
    assert.equal(recovered.displayName, interruptedBody.displayName);
    assert.deepEqual(expectStatus(await send(port, interrupted.clone())), recovered);
    expectStatus(await send(port, await patch(alice, { displayName: 'Changed Interrupted Intent' }, interruptedOperation)), 409);
    const concurrentOperation = crypto.randomUUID();
    const concurrent = await Promise.all(Array.from({ length: 8 }, async () => send(port,
      await patch(alice, interruptedBody, concurrentOperation))));
    for (const response of concurrent) assert.deepEqual(expectStatus(response), recovered);
    const repeated = () => read(alice);
    let last;
    for (let attempt = 0; attempt < 61; attempt++) last = await repeated();
    expectStatus(last);
    await stop(child, 'SIGKILL'); child = undefined;
    child = await start(runtime.serverPath, directory, port);
    expectStatus(await repeated());
    assert.equal(expectStatus(await read(alice)).displayName, interruptedBody.displayName);
    expectStatus(await read(bob), 401);
    expectStatus(await read(retired), 401); expectStatus(await read(revoked), 401);
    expectStatus(await send(port, await patch(alice, { displayName: 'Nonce Must Not Replay' }, crypto.randomUUID(), nonce)), 409);
    expectStatus(await send(port, replay.clone()));
    assert.equal(expectStatus(await read(alice)).displayName, interruptedBody.displayName);
    await stop(child, 'SIGTERM'); child = undefined;
    const { DatabaseSync } = await import('node:sqlite');
    const stored = new DatabaseSync(join(directory, 'restart.sqlite'));
    try {
      assert.ok(stored.prepare('SELECT COUNT(*) AS n FROM moderation_actions').get().n > 0);
      assert.ok(stored.prepare('SELECT COUNT(*) AS n FROM operations').get().n > 0);
      assert.ok(stored.prepare('SELECT COUNT(*) AS n FROM usage_observations').get().n > 0);
      assert.equal(stored.prepare('SELECT COUNT(*) AS n FROM operations WHERE operation_id = ?').get(interruptedOperation).n, 1);
      assert.equal(stored.prepare('SELECT COUNT(*) AS n FROM operations WHERE operation_id = ?').get(concurrentOperation).n, 1);
      assert.equal(stored.prepare('SELECT COUNT(*) AS n FROM profile_name_changes WHERE operation_id = ?').get(interruptedOperation).n, 1);
      stored.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
    } finally { stored.close(); }
    child = await start(runtime.serverPath, directory, port);
    expectStatus(await read(alice));
    expectStatus(await read(bob), 401);
    expectStatus(await read(retired), 401); expectStatus(await read(revoked), 401);
    await stop(child, 'SIGTERM'); child = undefined;
  } finally {
    if (child) await stop(child, 'SIGKILL');
    rmSync(directory, { recursive: true, force: true });
  }
}
