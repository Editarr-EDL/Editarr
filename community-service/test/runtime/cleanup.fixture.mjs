import assert from 'node:assert/strict';
import { mock } from 'node:test';
import { journey, expectStatus, patch, timestamp } from './journey.fixture.mjs';
import { startMaintenance } from '../../src/runtime/maintenance.mjs';

export async function exercise(database, runtime) {
  await journey(database, async ({ enroll, send, env, runtime: current }) => {
    const key = await enroll('1554889314549243999');
    expectStatus(await send(await patch(key, { displayName: 'Synthetic Cleanup Person' })));
    const operations = await database.prepare('SELECT * FROM operations ORDER BY operation_id').all();
    const expired = timestamp() - 10;
    await database.batch(Array.from({ length: 1002 }, (_, index) => database.prepare(
      'INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
      .bind(key.keyId, `expired-${index}`, expired, key.contributorId)));
    await database.prepare(`INSERT INTO discord_oauth_states (state_id, browser_hash, installation_id, key_id, public_key, expires_at)
      VALUES ('expired-oauth', 'synthetic', ?, ?, ?, 1)`).bind(key.installationId, key.keyId, key.publicKey).run();
    const counter = () => database.prepare("SELECT units FROM oauth_usage_counters WHERE resource = 'd1_writes'").first('units');
    const before = await counter();
    database.connection.exec(`CREATE TRIGGER synthetic_cleanup_failure BEFORE DELETE ON request_nonces
      BEGIN SELECT RAISE(ABORT, 'synthetic_cleanup_failure'); END`);
    mock.timers.enable({ apis: ['setInterval'] });
    let failures = 0, completed;
    const scheduled = { scheduled: (...args) => {
      const work = current.service.scheduled(...args);
      completed = work.catch(() => {});
      return work;
    } };
    const runner = (current.startMaintenance ?? startMaintenance)(scheduled, env, true, () => { failures++; });
    const tick = async () => {
      mock.timers.tick(3600000);
      await new Promise(resolve => setImmediate(resolve));
      await completed;
      await new Promise(resolve => setImmediate(resolve));
    };
    try {
      await tick();
      assert.equal(failures, 1);
      assert.equal(await database.prepare("SELECT COUNT(*) AS n FROM discord_oauth_states WHERE state_id = 'expired-oauth'").first('n'), 0);
      assert.ok(await counter() > before, 'partial cleanup writes remain accounted on failure');
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces WHERE expires_at = ?').bind(expired).first('n'), 1002);
      database.connection.exec('DROP TRIGGER synthetic_cleanup_failure');
      await tick();
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces WHERE expires_at = ?').bind(expired).first('n'), 2);
      await tick();
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces WHERE expires_at = ?').bind(expired).first('n'), 0);
      assert.equal(failures, 1);
      assert.deepEqual((await database.prepare('SELECT * FROM operations ORDER BY operation_id').all()).results, operations.results);
      assert.ok(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces').first('n') > 0);
      assert.equal(await database.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(key.keyId).first('state'), 'active');
    } finally { await runner.stop(); mock.timers.reset(); }
  }, runtime);
}
