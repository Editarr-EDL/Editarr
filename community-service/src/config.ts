import { z } from 'zod';
import type { SqlDatabase, AssetFetcher } from './runtime/contracts';
import { parseMasterPolicy, type MasterPolicy } from './filters/policy';
export interface Env {
  CATALOG_DB?: SqlDatabase;
  APP_ENV?: string;
  SELF_HOSTED_ENABLED?: boolean;
  TRUSTED_ACTOR_KEY?: (request: Request) => string | null;
  PUBLIC_ORIGIN?: string;
  POLICY_VERSION?: string;
  SUBMISSION_LIMITS?: string;
  VOTE_LIMITS?: string;
  REPORT_LIMITS?: string;
  MODERATION_LIMITS?: string;
  MASTER_POLICY?: string;
  DOWNLOAD_RECEIPT_PRIVATE_KEY?: string;
  DOWNLOAD_RECEIPT_KEYS?: string;
  DISCORD_CLIENT_ID?: string;
  DISCORD_CLIENT_SECRET?: string;
  DISCORD_GUILD_ID?: string;
  DISCORD_BOT_TOKEN?: string;
  ACCESS_AUDIENCE?: string;
  ACCESS_ISSUER?: string;
  OPERATOR_ORIGIN?: string;
  OPERATOR_SUBJECTS?: string;
  OPERATOR_ASSETS?: Pick<AssetFetcher, 'fetch'>;
}

export function missingConfig(env: Env): string[] {
  const required: (keyof Env)[] = ['CATALOG_DB', 'POLICY_VERSION'];
  if (env.APP_ENV !== 'local') {
    required.push('APP_ENV', 'PUBLIC_ORIGIN', 'MASTER_POLICY', 'DISCORD_CLIENT_ID',
      'DISCORD_CLIENT_SECRET', 'DISCORD_GUILD_ID', 'ACCESS_AUDIENCE',
      'ACCESS_ISSUER', 'OPERATOR_ORIGIN', 'OPERATOR_SUBJECTS', 'OPERATOR_ASSETS');
  }
  return required.filter((key) => !env[key]);
}

/** A missing or invalid required configuration value; routes answer 503. */
export class ConfigurationError extends Error {
  constructor(public code: 'master_policy_missing' | 'master_policy_invalid') { super(code); }
}

let parsedPolicy: { source: string; policy: MasterPolicy } | undefined;

/**
 * The strict `MASTER_POLICY` (§8.1), parsed once per configured value. Startup validation
 * parses it first; a missing or invalid value fails closed with `ConfigurationError`.
 */
export function masterPolicy(env: Pick<Env, 'MASTER_POLICY'>): MasterPolicy {
  const source = env.MASTER_POLICY;
  if (!source) throw new ConfigurationError('master_policy_missing');
  if (parsedPolicy?.source !== source) {
    try { parsedPolicy = { source, policy: parseMasterPolicy(JSON.parse(source)) }; }
    catch { throw new ConfigurationError('master_policy_invalid'); }
  }
  return parsedPolicy.policy;
}

/** `VOTE_LIMITS` and `REPORT_LIMITS`: request body and handler query bounds. */
export const routeLimits = z.strictObject({ requestBytes: z.number().int().min(1).max(8192),
  maxQueries: z.number().int().min(1).max(64) });
export type RouteLimits = z.infer<typeof routeLimits>;
