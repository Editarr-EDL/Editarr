import { expect, it } from 'vitest';
import { env } from './helpers/environment';
import identityMigration from '../migrations/0001_identity_operations.sql?raw';
import usageMigration from '../migrations/0003_quotas.sql?raw';
import { recordUsage, usageStatus } from '../src/budgets/usage';

it('records activity without consulting a policy or charging an allowance', async () => {
  const db = env.CATALOG_DB;
  await db.exec(identityMigration);
  await db.exec(usageMigration);
  await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at)
    VALUES ('observer', 'discord', 'Observer', 1)`).run();
  const now = Date.parse('2026-10-07T12:00:00Z');
  expect((await usageStatus(db, 'observer', 'd1_writes', now)).observations).toEqual({});
  for (const units of [1000000, 2000000]) {
    await recordUsage(db, { contributorId: 'observer', resource: 'd1_writes', outcome: 'allowed', units, now });
  }
  expect((await usageStatus(db, 'observer', 'd1_writes', now)).observations.allowed)
    .toEqual({ occurrences: 2, units: 3000000, observedAt: now });
  expect((await db.prepare('SELECT * FROM quota_charges').all()).results).toEqual([]);
});