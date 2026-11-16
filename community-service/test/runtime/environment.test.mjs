import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { readConfiguration, tunnelActor } from '../../src/runtime/environment.mjs';

test('starts disabled without configuration and cannot trust forwarded headers implicitly', () => {
  const config = readConfiguration();
  assert.equal(config.env.APP_ENV, 'self-hosted');
  assert.equal(config.env.SELF_HOSTED_ENABLED, false);
  assert.equal(config.trustedTunnel, false);
  assert.equal(tunnelActor(new Request('https://service.test')), null);
  assert.equal(tunnelActor(new Request('https://service.test', { headers: { 'CF-Connecting-IP': 'not-an-ip' } })), null);
  assert.equal(tunnelActor(new Request('https://service.test', { headers: { 'CF-Connecting-IP': '192.0.2.1' } })), 'cloudflare-ip:192.0.2.1');
  assert.equal(tunnelActor(new Request('https://service.test', { headers: { 'CF-Connecting-IP': '192.0.2.1', 'CF-Worker': 'other.test' } })), null);
});

test('container preflight refuses disabled or invalid configuration without opening a database or exposing values', () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-preflight-'));
  const path = join(directory, 'service.json');
  const database = join(directory, 'must-not-exist.sqlite');
  try {
    for (const config of [{ enabled: false, trustedTunnel: false, requestBytes: 8192, variables: {} },
      { enabled: true, trustedTunnel: false, requestBytes: 8192, variables: { POLICY_VERSION: 'private-preflight-value' } }]) {
      writeFileSync(path, JSON.stringify(config), { mode: 0o600 });
      const result = spawnSync(process.execPath, [new URL('../../scripts/check-container.mjs', import.meta.url).pathname], {
        env: { PATH: process.env.PATH, COMMUNITY_CONFIG_FILE: path, COMMUNITY_DATABASE: database }, encoding: 'utf8',
      });
      assert.equal(result.status, 1);
      assert.match(result.stderr, /community_preflight_refused/);
      assert.ok(!result.stderr.includes('private-preflight-value'));
      assert.deepEqual(result.stdout, '');
      assert.throws(() => readFileSync(database), /ENOENT/);
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('explicit activation requires protected tunnel trust and does not accept runtime-binding injection', () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-config-'));
  const path = join(directory, 'service.json'), secretPath = join(directory, 'secrets.json');
  const config = { enabled: true, trustedTunnel: false, requestBytes: 8192, variables: {} };
  const secrets = { DISCORD_CLIENT_SECRET: 'synthetic', DISCORD_BOT_TOKEN: 'synthetic', DOWNLOAD_RECEIPT_PRIVATE_KEY: 'synthetic' };
  try {
    writeFileSync(path, JSON.stringify(config), { mode: 0o600 });
    writeFileSync(secretPath, JSON.stringify(secrets), { mode: 0o600 });
    assert.throws(() => readConfiguration(path, secretPath), /protected_tunnel_and_secrets_required/);
    config.trustedTunnel = true;
    writeFileSync(path, JSON.stringify(config));
    assert.equal(readConfiguration(path, secretPath).env.SELF_HOSTED_ENABLED, true);
    assert.throws(() => readConfiguration(path), /protected_tunnel_and_secrets_required/);
    config.variables.APP_ENV = 'local';
    writeFileSync(path, JSON.stringify(config));
    assert.throws(() => readConfiguration(path, secretPath), /invalid_configuration_variables/);
    delete config.variables.APP_ENV;
    config.requestBytes = 3000001;
    writeFileSync(path, JSON.stringify(config));
    assert.throws(() => readConfiguration(path, secretPath), /invalid_service_configuration/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});