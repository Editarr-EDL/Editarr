import { env } from './environment';
import { expect } from 'vitest';
import identityMigration from '../../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../../migrations/0003_quotas.sql?raw';
import admissionMigration from '../../migrations/0004_request_admission.sql?raw';
import oauthMigration from '../../migrations/0005_discord_oauth.sql?raw';
import usageMigration from '../../migrations/0006_oauth_usage.sql?raw';
import membershipMigration from '../../migrations/0007_membership.sql?raw';
import profileMigration from '../../migrations/0008_enrollment_profiles.sql?raw';

export async function verifyProfileUpgradeRefusal(names: string[]) {
  const db = env.CATALOG_DB;
  for (const sql of [identityMigration, budgetMigration, quotaMigration, admissionMigration,
    oauthMigration, usageMigration, membershipMigration]) await db.exec(sql.replace(/\s*\n\s*/g, ' '));
  for (const [index, name] of names.entries()) {
    await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, state, created_at)
      VALUES (?, ?, ?, 'suspended', 1)`).bind(`preserved-${index}`, `private-${index}`, name).run();
  }
  await expect(db.exec(profileMigration.replace(/\s*\n\s*/g, ' '))).rejects.toThrow();
  expect((await db.prepare('SELECT display_name, state FROM contributors ORDER BY contributor_id').all()).results)
    .toEqual(names.map((display_name) => ({ display_name, state: 'suspended' })));
}