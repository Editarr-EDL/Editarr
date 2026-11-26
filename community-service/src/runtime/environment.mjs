import { readFileSync, lstatSync } from 'node:fs';
import { isIP } from 'node:net';

const variables = new Set(['PUBLIC_ORIGIN', 'POLICY_VERSION', 'SUBMISSION_LIMITS',
  'VOTE_LIMITS', 'REPORT_LIMITS', 'MODERATION_LIMITS', 'MASTER_POLICY',
  'DOWNLOAD_RECEIPT_KEYS', 'DISCORD_CLIENT_ID', 'DISCORD_GUILD_ID', 'ACCESS_AUDIENCE',
  'ACCESS_ISSUER', 'OPERATOR_ORIGIN', 'OPERATOR_SUBJECTS']);
const secrets = new Set(['DISCORD_CLIENT_SECRET', 'DISCORD_BOT_TOKEN', 'DOWNLOAD_RECEIPT_PRIVATE_KEY']);

function jsonFile(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.size > 131072) throw new Error('invalid_configuration_file');
  return JSON.parse(readFileSync(path, 'utf8'));
}

function strings(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.entries(value).some(([key, item]) => !allowed.has(key) || typeof item !== 'string' || item.length > 65536)) {
    throw new Error('invalid_configuration_variables');
  }
  return value;
}

export function readConfiguration(path, secretsPath) {
  const config = path ? jsonFile(path) : { enabled: false, variables: {}, requestBytes: 8192, trustedTunnel: false };
  if (!config || typeof config !== 'object' || Array.isArray(config) ||
      Object.keys(config).some(key => !['enabled', 'variables', 'requestBytes', 'trustedTunnel'].includes(key)) ||
      typeof config.enabled !== 'boolean' || typeof config.trustedTunnel !== 'boolean' ||
      !Number.isSafeInteger(config.requestBytes) || config.requestBytes < 1 || config.requestBytes > 3000000) {
    throw new Error('invalid_service_configuration');
  }
  const values = strings(config.variables, variables);
  const protectedValues = secretsPath ? strings(jsonFile(secretsPath), secrets) : {};
  if (config.enabled && (!config.trustedTunnel || [...secrets].some(key => !protectedValues[key]))) {
    throw new Error('protected_tunnel_and_secrets_required');
  }
  return { ...config, env: { ...values, ...protectedValues, APP_ENV: 'self-hosted', SELF_HOSTED_ENABLED: config.enabled } };
}

export function tunnelActor(request) {
  const address = request.headers.get('CF-Connecting-IP');
  if (!address || !isIP(address) || request.headers.has('CF-Worker')) return null;
  const host = new URL(address.includes(':') ? `https://[${address}]/` : `https://${address}/`).hostname;
  return `cloudflare-ip:${host}`;
}
