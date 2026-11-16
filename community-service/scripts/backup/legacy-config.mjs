import { CliError } from '../operator/transport.mjs';
import { digest } from './validate.mjs';
import { loadReceiptSigner } from './artifact-runtime.mjs';

export const secretNames = ['DISCORD_BOT_TOKEN', 'DISCORD_CLIENT_SECRET', 'DOWNLOAD_RECEIPT_PRIVATE_KEY'];
export const configurationVariables = ['APP_ENV', 'PUBLIC_ORIGIN', 'POLICY_VERSION', 'PUBLICATION_LIMITS', 'LOOKUP_LIMITS',
  'DOWNLOAD_LIMITS', 'IMPORT_LIMITS', 'VOTE_LIMITS', 'REPORT_LIMITS', 'MODERATION_LIMITS', 'DOWNLOAD_RECEIPT_KEYS',
  'DISCORD_CLIENT_ID', 'DISCORD_GUILD_ID', 'ACCESS_AUDIENCE', 'ACCESS_ISSUER', 'OPERATOR_ORIGIN', 'OPERATOR_SUBJECTS'];

export async function validateInputs(configBytes, secretsBytes, options) {
  let config, secrets;
  try {
    config = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(configBytes));
    secrets = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(secretsBytes));
    if (Object.keys(config).sort().join(',') !== 'bindings,deployment,vars' ||
        Object.keys(config.deployment).sort().join(',') !== 'releaseVersion,revision,workerVersion' ||
        Object.values(config.deployment).some(value => typeof value !== 'string' || !value || value.length > 256) ||
        Object.keys(config.vars).sort().join(',') !== [...configurationVariables].sort().join(',') ||
        Object.values(config.vars).some(value => value !== null && (typeof value !== 'string' || value.length > 65536)) ||
        Object.keys(config.bindings).sort().join(',') !== 'CATALOG_DB,OAUTH_IP_LIMITER,OAUTH_SHARED_LIMITER,OPERATOR_ASSETS' ||
        Object.keys(config.bindings.CATALOG_DB).sort().join(',') !== 'accountId,databaseId' ||
        config.bindings.CATALOG_DB.accountId !== options.account || config.bindings.CATALOG_DB.databaseId !== options.database ||
        secretNames.some(key => typeof secrets[key] !== 'string' || !secrets[key] || secrets[key].length > 16384) ||
        Object.keys(secrets).sort().join(',') !== [...secretNames].sort().join(',')) throw new Error();
    const assets = config.bindings.OPERATOR_ASSETS;
    if (assets !== null && (Object.keys(assets).join(',') !== 'sha256' || !/^[a-f0-9]{64}$/.test(assets.sha256))) throw new Error();
    for (const name of ['OAUTH_IP_LIMITER', 'OAUTH_SHARED_LIMITER']) {
      const binding = config.bindings[name];
      if (binding !== null && (Object.keys(binding).sort().join(',') !== 'limit,namespaceId,period' ||
          typeof binding.namespaceId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(binding.namespaceId) ||
          !Number.isSafeInteger(binding.limit) || binding.limit <= 0 || binding.period !== 60)) throw new Error();
    }
    await loadReceiptSigner(secrets.DOWNLOAD_RECEIPT_PRIVATE_KEY, JSON.parse(config.vars.DOWNLOAD_RECEIPT_KEYS));
  } catch { throw new CliError('backup_configuration_or_secrets_invalid'); }
  return { config, secretsBytes, inputDigest: digest(Buffer.concat([configBytes, Buffer.from([0]), secretsBytes])) };
}