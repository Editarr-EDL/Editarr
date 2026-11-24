import service from '../index';
import { masterPolicy, type Env } from '../config';
import { operatorConfig } from '../admin/auth/access';
import { moderationLimits } from '../admin/admission';
import { publicationLimits } from '../contributions/publication-input';
import { searchLimits } from '../catalog/search-input';
import { downloadLimits } from '../contributions/download';
import { importLimits } from '../http/imports';
import { voteLimits } from '../contributions/votes';
import { reportLimits } from '../contributions/reports';
import { loadReceiptSigner, receiptKeysSchema } from '../contributions/receipts';

export { previewRetention } from '../retention/policy';

export async function validateEnvironment(env: Env) {
  const origin = new URL(env.PUBLIC_ORIGIN ?? '');
  if (origin.protocol !== 'https:' || origin.origin !== env.PUBLIC_ORIGIN || origin.username || origin.password ||
      !/^\d{17,20}$/.test(env.DISCORD_CLIENT_ID ?? '') || !/^\d{17,20}$/.test(env.DISCORD_GUILD_ID ?? '') ||
      !env.DISCORD_CLIENT_SECRET || !env.DISCORD_BOT_TOKEN || !env.POLICY_VERSION) throw new Error('invalid_service_configuration');
  operatorConfig(env);
  publicationLimits(JSON.parse(env.PUBLICATION_LIMITS ?? ''));
  searchLimits(JSON.parse(env.LOOKUP_LIMITS ?? ''));
  downloadLimits(JSON.parse(env.DOWNLOAD_LIMITS ?? ''));
  importLimits.parse(JSON.parse(env.IMPORT_LIMITS ?? ''));
  voteLimits.parse(JSON.parse(env.VOTE_LIMITS ?? ''));
  reportLimits.parse(JSON.parse(env.REPORT_LIMITS ?? ''));
  moderationLimits.parse(JSON.parse(env.MODERATION_LIMITS ?? ''));
  masterPolicy(env);
  const keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS ?? ''));
  await loadReceiptSigner(env.DOWNLOAD_RECEIPT_PRIVATE_KEY ?? '', keys);
}

export default service;