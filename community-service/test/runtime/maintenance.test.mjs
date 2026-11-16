import assert from 'node:assert/strict';
import { test } from 'node:test';
import { startMaintenance } from '../../src/runtime/maintenance.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));

test('hourly timer does not run early or overlap and shutdown drains cleanup', async context => {
  context.mock.timers.enable({ apis: ['setInterval'] });
  let calls = 0, finish;
  const service = { scheduled: () => { calls++; return new Promise(resolve => { finish = resolve; }); } };
  const runner = startMaintenance(service, {}, true);
  context.mock.timers.tick(3599999); await flush(); assert.equal(calls, 0);
  context.mock.timers.tick(1); await flush(); assert.equal(calls, 1);
  context.mock.timers.tick(7200000); await flush(); assert.equal(calls, 1);
  let stopped = false;
  const draining = runner.stop().then(() => { stopped = true; });
  await flush(); assert.equal(stopped, false);
  finish(); await draining;
  context.mock.timers.tick(3600000); await flush(); assert.equal(calls, 1);
});

test('disabled scheduling stays idle; sync and async failures are redacted and retry next hour', async context => {
  context.mock.timers.enable({ apis: ['setInterval'] });
  let calls = 0, failures = 0;
  const service = { scheduled: () => {
    calls++;
    if (calls === 1) throw new Error('sensitive detail');
    return Promise.reject(new Error('other sensitive detail'));
  } };
  const disabled = startMaintenance(service, {}, false);
  context.mock.timers.tick(3600000); await flush(); assert.equal(calls, 0); await disabled.stop();
  const enabled = startMaintenance(service, {}, true, (...args) => { assert.deepEqual(args, []); failures++; });
  context.mock.timers.tick(3600000); await flush(); assert.equal(failures, 1);
  context.mock.timers.tick(3600000); await flush(); assert.equal(failures, 2);
  await enabled.stop();
});
