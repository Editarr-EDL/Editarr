import { env } from './helpers/environment';
import { describe, expect, it } from 'vitest';

import { missingConfig } from '../src/config';
import service from '../src/index';

describe('local Node service scaffold', () => {
  it('answers health through the runtime and fails closed on capabilities', async () => {
    const health = await service.fetch(new Request('http://example.invalid/health'), env);
    expect(health.status).toBe(200);
    expect(await health.json()).toEqual({ status: 'ok' });

    const capabilities = await service.fetch(new Request('http://example.invalid/v1/capabilities'), env);
    expect(capabilities.status).toBe(503);
    expect(await capabilities.json()).toEqual({ error: { code: 'service_unavailable' } });
  });

  it('provides isolated local SQLite binding', async () => {
    expect((await env.CATALOG_DB.prepare('SELECT 1 AS ready').first<{ ready: number }>())?.ready)
      .toBe(1);
    expect('ARTIFACTS' in env).toBe(false);
  });

  it('denies production admission without policy and credentials', async () => {
    expect(missingConfig({ APP_ENV: 'production', CATALOG_DB: env.CATALOG_DB, })).toEqual([
      'POLICY_VERSION', 'PUBLIC_ORIGIN', 'MASTER_POLICY', 'DISCORD_CLIENT_ID',
      'DISCORD_CLIENT_SECRET', 'DISCORD_GUILD_ID', 'ACCESS_AUDIENCE',
      'ACCESS_ISSUER', 'OPERATOR_ORIGIN', 'OPERATOR_SUBJECTS', 'OPERATOR_ASSETS',
    ]);
    const response = await service.fetch(new Request('http://example.invalid/v1/capabilities'), {
      APP_ENV: 'production', CATALOG_DB: env.CATALOG_DB,
      DISCORD_CLIENT_SECRET: 'fixture-secret',
    });
    expect(response.status).toBe(503);
    const safeBody = response.clone();
    expect(await response.json()).toEqual({ error: { code: 'service_unavailable' } });
    expect(await safeBody.text()).not.toContain('fixture-secret');
    expect((await service.fetch(new Request('http://example.invalid/health'), {
      APP_ENV: 'production', CATALOG_DB: env.CATALOG_DB,
    })).status).toBe(503);
    expect((await service.fetch(new Request('http://example.invalid/health'), {
      APP_ENV: 'production', POLICY_VERSION: 'fixture', PUBLIC_ORIGIN: 'https://example.invalid',
      DISCORD_CLIENT_ID: 'fixture', DISCORD_CLIENT_SECRET: 'fixture-secret',
      ACCESS_AUDIENCE: 'fixture', CATALOG_DB: env.CATALOG_DB,
    })).status).toBe(503);
    expect((await service.fetch(new Request('http://example.invalid/admin/v1'), env)).status)
      .toBe(503);
  });
});