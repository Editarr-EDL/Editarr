import type { SqlDatabase, AssetFetcher } from './runtime/contracts';
export interface Env {
  CATALOG_DB?: SqlDatabase;
  APP_ENV?: string;
  SELF_HOSTED_ENABLED?: boolean;
  TRUSTED_ACTOR_KEY?: (request: Request) => string | null;
  PUBLIC_ORIGIN?: string;
  POLICY_VERSION?: string;
  PUBLICATION_LIMITS?: string;
  LOOKUP_LIMITS?: string;
  DOWNLOAD_LIMITS?: string;
  IMPORT_LIMITS?: string;
  VOTE_LIMITS?: string;
  REPORT_LIMITS?: string;
  MODERATION_LIMITS?: string;
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
    required.push('APP_ENV', 'PUBLIC_ORIGIN', 'DISCORD_CLIENT_ID',
      'DISCORD_CLIENT_SECRET', 'DISCORD_GUILD_ID', 'ACCESS_AUDIENCE',
      'ACCESS_ISSUER', 'OPERATOR_ORIGIN', 'OPERATOR_SUBJECTS', 'OPERATOR_ASSETS');
  }
  return required.filter((key) => !env[key]);
}