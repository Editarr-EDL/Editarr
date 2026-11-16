import { parseDictionary, serializeDictionary, type InnerList } from 'structured-headers';

export class RequestSignatureError extends Error {}

export class RequestSignatureTimeError extends RequestSignatureError {}

const LABEL = 'editarr';
const TAG = 'editarr-community-request-v1';
const READ_COMPONENTS = ['@method', '@scheme', '@authority', '@path', '@query',
  'content-digest'];
const WRITE_COMPONENTS = [...READ_COMPONENTS, 'content-type', 'idempotency-key'];
const encoder = new TextEncoder();

export interface VerificationPolicy {
  now: number;
  maxLifetimeSeconds: number;
  maxClockSkewSeconds: number;
  expectedAuthority: string;
  expectedKeyId: string;
}

function singleDictionary(value: string, label: string) {
  if (value.length > 2048) throw new RequestSignatureError('Oversized signature field');
  try {
    const parsed = parseDictionary(value);
    if (parsed.size !== 1 || !parsed.has(label) || serializeDictionary(parsed) !== value) {
      throw new RequestSignatureError('Ambiguous or noncanonical signature field');
    }
    return parsed.get(label)!;
  } catch {
    throw new RequestSignatureError('Invalid signature field');
  }
}

function header(headers: Headers, name: string, structured = false): string {
  const value = headers.get(name);
  if (value === null || value.length > (structured ? 2048 : 256) ||
    !(structured ? /^[\x20-\x7e]+$/ : /^[\x21-\x7e]+$/).test(value)) {
    throw new RequestSignatureError(`Invalid ${name} field`);
  }
  return value;
}

function canonicalNonce(nonce: unknown): nonce is string {
  if (typeof nonce !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(nonce)) return false;
  try {
    const bytes = Uint8Array.from(atob(nonce.replace(/-/g, '+').replace(/_/g, '/') + '=='),
      (char) => char.charCodeAt(0));
    return bytes.length === 16 && btoa(String.fromCharCode(...bytes)).replace(/=/g, '')
      .replace(/\+/g, '-').replace(/\//g, '_') === nonce;
  } catch {
    return false;
  }
}

function targetComponents(url: string, expectedAuthority: string) {
  if (!/^[\x21-\x7e]+$/.test(url) || /%(?![0-9A-Fa-f]{2})/.test(url)) {
    throw new RequestSignatureError('Invalid target URI spelling');
  }
  let target: URL;
  try {
    target = new URL(url);
  } catch {
    throw new RequestSignatureError('Invalid target URI');
  }
  if (target.protocol !== 'https:' || target.origin !== `https://${expectedAuthority}` ||
    target.href !== url || target.username || target.password || target.hash ||
    /%2[eEfF]|(^|\/)\.\.?(\/|$)/.test(target.pathname) || target.pathname.startsWith('//')) {
    throw new RequestSignatureError('Target authority or path mismatch');
  }
  return { '@scheme': 'https', '@authority': target.host, '@path': target.pathname,
    '@query': target.search || '?' };
}

export async function verifyRequestSignature(request: {
  method: string; url: string; headers: Headers; body: Uint8Array;
}, rawPublicKey: Uint8Array, policy: VerificationPolicy) {
  if (!Number.isSafeInteger(policy.now) || !Number.isSafeInteger(policy.maxLifetimeSeconds) ||
    !Number.isSafeInteger(policy.maxClockSkewSeconds) || policy.maxLifetimeSeconds <= 0 ||
    policy.maxClockSkewSeconds < 0 || rawPublicKey.byteLength !== 32) {
    throw new RequestSignatureError('Invalid verifier configuration');
  }
  const input = singleDictionary(header(request.headers, 'signature-input', true), LABEL);
  const signatureItem = singleDictionary(header(request.headers, 'signature', true), LABEL);
  if (!Array.isArray(input[0]) || !(input[1] instanceof Map) ||
    Array.isArray(signatureItem[0]) || signatureItem[1].size !== 0 ||
    !(signatureItem[0] instanceof ArrayBuffer) || signatureItem[0].byteLength !== 64) {
    throw new RequestSignatureError('Invalid signature structure');
  }
  const [covered, parameters] = input as InnerList;
  const names = covered.map(([name, params]) => {
    if (typeof name !== 'string' || params.size !== 0) {
      throw new RequestSignatureError('Unsupported covered component');
    }
    return name;
  });
  const mutation = request.method !== 'GET';
  if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method)) {
    throw new RequestSignatureError('Unsupported HTTP method');
  }
  const encoding = request.headers.has('content-encoding') ?
    header(request.headers, 'content-encoding') : null;
  const expected = mutation ? [...WRITE_COMPONENTS, ...(encoding ? ['content-encoding'] : [])] :
    READ_COMPONENTS;
  if (names.length !== expected.length || names.some((name, index) => name !== expected[index])) {
    throw new RequestSignatureError('Unexpected covered components');
  }
  const requiredParameters = ['created', 'expires', 'nonce', 'keyid', 'alg', 'tag'];
  if (Array.from(parameters.keys()).join(',') !== requiredParameters.join(',') ||
    !Number.isSafeInteger(parameters.get('created')) ||
    !Number.isSafeInteger(parameters.get('expires')) ||
    parameters.get('alg') !== 'ed25519' || parameters.get('tag') !== TAG ||
    parameters.get('keyid') !== policy.expectedKeyId ||
    !canonicalNonce(parameters.get('nonce'))) {
    throw new RequestSignatureError('Invalid signature parameters');
  }
  const created = parameters.get('created') as number;
  const expires = parameters.get('expires') as number;
  if (created < 0 || expires <= created || expires - created > policy.maxLifetimeSeconds) {
    throw new RequestSignatureError('Signature outside allowed time window');
  }
  const target = targetComponents(request.url, policy.expectedAuthority);
  if (!mutation && (request.body.length !== 0 || request.headers.has('content-type') || encoding)) {
    throw new RequestSignatureError('Read request has unexpected content metadata');
  }
  const operationId = mutation ? header(request.headers, 'idempotency-key') : null;
  const contentType = mutation ? header(request.headers, 'content-type') : null;
  if (mutation && (!/^[A-Za-z0-9._~-]{1,128}$/.test(operationId!) ||
    !['application/json', 'application/octet-stream'].includes(contentType!) ||
    (encoding && encoding !== 'gzip'))) {
    throw new RequestSignatureError('Invalid mutation content metadata');
  }
  if (!mutation && request.headers.has('idempotency-key')) {
    throw new RequestSignatureError('Read request cannot have an operation key');
  }
  const digest = await crypto.subtle.digest('SHA-256', request.body as BufferSource);
  const digestValue = `sha-256=:${btoa(String.fromCharCode(...new Uint8Array(digest)))}:`;
  if (header(request.headers, 'content-digest') !== digestValue) {
    throw new RequestSignatureError('Content digest does not match transmitted bytes');
  }
  const values: Record<string, string | null> = {
    '@method': request.method, ...target, 'content-digest': digestValue,
    'content-type': contentType, 'content-encoding': encoding, 'idempotency-key': operationId,
  };
  const signatureParameters = serializeDictionary(new Map([[LABEL, input]])).slice(LABEL.length + 1);
  const base = names.map((name) => `"${name}": ${values[name]}`).join('\n') +
    `\n"@signature-params": ${signatureParameters}`;
  let valid = false;
  try {
    const publicKey = await crypto.subtle.importKey('raw', rawPublicKey as BufferSource,
      'Ed25519', false, ['verify']);
    valid = await crypto.subtle.verify('Ed25519', publicKey, signatureItem[0] as ArrayBuffer,
      encoder.encode(base));
  } catch {
    throw new RequestSignatureError('Invalid Ed25519 public key');
  }
  if (!valid) throw new RequestSignatureError('Signature verification failed');
  if (created > policy.now + policy.maxClockSkewSeconds ||
    policy.now > expires + policy.maxClockSkewSeconds) {
    throw new RequestSignatureTimeError('Signature outside allowed time window');
  }
  return { keyId: policy.expectedKeyId, nonce: parameters.get('nonce') as string,
    operationId, created, expires, contentDigest: digestValue };
}