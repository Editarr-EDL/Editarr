import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

export class CliError extends Error {
  constructor(code, status = 0) { super(code); this.code = code; this.status = status; }
}

export function operatorOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.origin !== value || url.hostname === 'workers.dev' ||
        url.hostname.endsWith('.workers.dev')) throw new Error();
    return url.origin;
  } catch { throw new CliError('invalid_operator_origin'); }
}

export async function accessToken(origin, execute = promisify(execFile)) {
  try {
    const { stdout } = await execute('cloudflared', ['access', 'token', `-app=${origin}/admin/`],
      { timeout: 15000, maxBuffer: 32768, encoding: 'utf8' });
    const token = stdout.trim();
    if (token.length > 16384 || !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(token)) throw new Error();
    return token;
  } catch { throw new CliError('access_login_required'); }
}

export function operatorClient(origin, token, fetcher = fetch) {
  operatorOrigin(origin);
  if (token.length > 16384 || !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(token))
    throw new CliError('access_login_required');
  return async (path, body) => {
    if (!/^\/admin\/v1\/[A-Za-z0-9_./?=&%+-]+$/.test(path) || path.includes('..') || path.includes('//'))
      throw new CliError('invalid_operator_path');
    const url = new URL(path, origin);
    if (url.origin !== origin) throw new CliError('invalid_operator_path');
    let response;
    try {
      response = await fetcher(url, { method: body === undefined ? 'GET' : 'POST', redirect: 'error',
        cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(15000),
        headers: { 'Cf-Access-Token': token, Accept: 'application/json',
          ...(body === undefined ? {} : { Origin: origin, 'X-Community-Operator': '1', 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    } catch { throw new CliError('operator_transport_unavailable'); }
    if (response.status >= 300 && response.status < 400) throw new CliError('operator_redirect_denied');
    if (!response.headers.get('content-type')?.startsWith('application/json'))
      throw new CliError('invalid_operator_response', response.status);
    let value;
    try {
      const reader = response.body?.getReader();
      if (!reader) throw new Error();
      const chunks = [];
      let length = 0;
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          length += part.value.length;
          if (length > 131072) throw new Error();
          chunks.push(Buffer.from(part.value));
        }
      } finally { await reader.cancel(); }
      value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
    } catch { throw new CliError('invalid_operator_response', response.status); }
    if (!response.ok) {
      const code = value?.error?.code;
      const safe = typeof code === 'string' && /^[a-z_]{1,80}$/.test(code) ? code : 'operator_request_denied';
      throw new CliError(safe, response.status);
    }
    return JSON.parse(JSON.stringify(value).replaceAll(token, '[REDACTED]'));
  };
}