import service from '../index';
import { masterPolicy, routeLimits, voteLimits, type Env } from '../config';
import { operatorConfig } from '../admin/auth/access';
import { moderationLimits } from '../admin/admission';
import { loadReceiptSigner, receiptKeysSchema } from '../security/service-signer';
import { submissionLimitsSchema } from '../submissions/input';

export { previewRetention } from '../retention/policy';
import { backupDrain, scheduledBackup } from '../backups/runner';
/** Maintenance-loop hooks for UI-configured online backups. */
export const backupScheduler = { tick: scheduledBackup, drain: backupDrain };

export async function validateEnvironment(env: Env) {
  const origin = new URL(env.PUBLIC_ORIGIN ?? '');
  if (origin.protocol !== 'https:' || origin.origin !== env.PUBLIC_ORIGIN || origin.username || origin.password ||
      !/^\d{17,20}$/.test(env.DISCORD_CLIENT_ID ?? '') || !/^\d{17,20}$/.test(env.DISCORD_GUILD_ID ?? '') ||
      !env.DISCORD_CLIENT_SECRET || !env.DISCORD_BOT_TOKEN || !env.POLICY_VERSION) throw new Error('invalid_service_configuration');
  operatorConfig(env);
  submissionLimitsSchema.parse(JSON.parse(env.SUBMISSION_LIMITS ?? ''));
  voteLimits.parse(JSON.parse(env.VOTE_LIMITS ?? ''));
  routeLimits.parse(JSON.parse(env.REPORT_LIMITS ?? ''));
  moderationLimits.parse(JSON.parse(env.MODERATION_LIMITS ?? ''));
  masterPolicy(env);
  const keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS ?? ''));
  await loadReceiptSigner(env.DOWNLOAD_RECEIPT_PRIVATE_KEY ?? '', keys);
}

export default service;
