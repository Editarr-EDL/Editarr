import assert from 'node:assert/strict';
import { test } from 'node:test';
import { startMaintenance } from '../../src/runtime/maintenance.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));

test('backup schedule is checked every minute without overlap and drained on shutdown', async context => {
  context.mock.timers.enable({ apis: ['setInterval', 'Date'], now: 1_000_000 });
  const ticks = [];
  let finish, drained = false, failures = 0;
  const backups = { tick: (env, now) => { ticks.push(now); return new Promise(resolve => { finish = resolve; }); },
    drain: async () => { drained = true; } };
  const runner = startMaintenance({ scheduled: async () => undefined }, { marker: 1 }, true, () => { failures++; }, backups);
  context.mock.timers.tick(59999); await flush(); assert.deepEqual(ticks, []);
  context.mock.timers.tick(1); await flush(); assert.deepEqual(ticks, [1_060_000]);
  context.mock.timers.tick(120000); await flush(); assert.equal(ticks.length, 1);
  finish(); await flush();
  context.mock.timers.tick(60000); await flush(); assert.equal(ticks.length, 2);
  finish();
  await runner.stop();
  assert.equal(drained, true);
  context.mock.timers.tick(600000); await flush(); assert.equal(ticks.length, 2);
  assert.equal(failures, 0);
});

test('backup checks stay idle while the service is disabled and survive failures', async context => {
  context.mock.timers.enable({ apis: ['setInterval'] });
  let calls = 0;
  const errors = context.mock.method(console, 'error', () => undefined);
  const backups = { tick: async () => { calls++; throw new Error('sensitive'); }, drain: async () => undefined };
  const disabled = startMaintenance({ scheduled: async () => undefined }, {}, false, undefined, backups);
  context.mock.timers.tick(600000); await flush(); assert.equal(calls, 0); await disabled.stop();
  const enabled = startMaintenance({ scheduled: async () => undefined }, {}, true, undefined, backups);
  context.mock.timers.tick(60000); await flush(); context.mock.timers.tick(60000); await flush();
  assert.equal(calls, 2);
  assert.deepEqual(errors.mock.calls.map(call => call.arguments), [['community_backup_failed'], ['community_backup_failed']]);
  await enabled.stop();
});
