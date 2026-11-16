import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from 'jose';
import { z } from 'zod';
import type { Env } from '../../config';

const subjects = z.array(z.string().min(1).max(256)).min(1).max(100);
export interface OperatorConfig {
  origin: string;
  issuer: string;
  audience: string;
  subjects: readonly string[];
}
export interface OperatorPrincipal { subject: string }
export class OperatorDenied extends Error {
  constructor(public readonly status: number, public readonly code: string) { super(code); }
}

export function operatorConfig(env: Env): OperatorConfig {
  try {
    const origin = new URL(env.OPERATOR_ORIGIN!);
    const issuer = new URL(env.ACCESS_ISSUER!);
    if (origin.protocol !== 'https:' || origin.origin !== env.OPERATOR_ORIGIN ||
        origin.hostname.endsWith('.workers.dev') || origin.hostname === 'workers.dev' ||
        issuer.protocol !== 'https:' || issuer.origin !== env.ACCESS_ISSUER ||
        !/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(issuer.hostname) || issuer.port ||
        !env.ACCESS_AUDIENCE || env.ACCESS_AUDIENCE.length > 256) throw new Error();
    return { origin: origin.origin, issuer: issuer.origin, audience: env.ACCESS_AUDIENCE,
      subjects: subjects.parse(JSON.parse(env.OPERATOR_SUBJECTS!)) };
  } catch { throw new OperatorDenied(503, 'operator_not_configured'); }
}

let remote: { issuer: string; resolver: JWTVerifyGetKey } | undefined;
function accessKeys(config: OperatorConfig): JWTVerifyGetKey {
  if (remote?.issuer !== config.issuer) {
    remote = { issuer: config.issuer, resolver: createRemoteJWKSet(
      new URL(`${config.issuer}/cdn-cgi/access/certs`), { timeoutDuration: 5000 },
    ) };
  }
  return remote.resolver;
}

export async function authenticateOperator(request: Request, config: OperatorConfig,
  resolver: JWTVerifyGetKey = accessKeys(config), now = new Date()): Promise<OperatorPrincipal> {
  if (new URL(request.url).origin !== config.origin) throw new OperatorDenied(403, 'operator_origin_denied');
  const token = request.headers.get('Cf-Access-Jwt-Assertion');
  if (!token || token.length > 16384 || !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(token))
    throw new OperatorDenied(401, 'operator_authentication_required');
  let subject: string;
  try {
    const { payload } = await jwtVerify(token, resolver, { algorithms: ['RS256'], issuer: config.issuer,
      audience: config.audience, requiredClaims: ['exp', 'iat', 'sub', 'iss', 'aud'], currentDate: now });
    if (typeof payload.sub !== 'string' || payload.sub.length > 256 || payload.type !== 'app' ||
        typeof payload.iat !== 'number' || payload.iat > now.getTime() / 1000) throw new Error();
    subject = payload.sub;
  } catch (error) {
    const code = (error as { code?: string }).code;
    if (code === 'ERR_JWKS_TIMEOUT' || error instanceof TypeError)
      throw new OperatorDenied(503, 'operator_verification_unavailable');
    throw new OperatorDenied(401, 'operator_token_invalid');
  }
  if (!config.subjects.includes(subject)) throw new OperatorDenied(403, 'operator_not_allowed');
  return { subject };
}

export function requireOperatorMutation(request: Request, config: OperatorConfig): void {
  if (request.headers.get('Origin') !== config.origin ||
      request.headers.get('X-Community-Operator') !== '1' ||
      (request.headers.has('Sec-Fetch-Site') && request.headers.get('Sec-Fetch-Site') !== 'same-origin'))
    throw new OperatorDenied(403, 'operator_csrf_denied');
}