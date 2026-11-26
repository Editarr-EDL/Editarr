import type { JWTVerifyGetKey } from 'jose';
import type { Env } from '../config';
import { authenticateOperator, OperatorDenied, operatorConfig, requireOperatorMutation } from './auth/access';
import { operatorModeration } from './moderation-http';
import { operatorDashboard } from './dashboard';

const securityHeaders = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
};

function json(status: number, code: string): Response {
  return Response.json({ error: { code } }, { status, headers: securityHeaders });
}

export async function operatorService(request: Request, env: Env,
  resolver?: JWTVerifyGetKey, now?: Date): Promise<Response> {
  try {
    const config = operatorConfig(env);
    const principal = await authenticateOperator(request, config, resolver, now);
    const path = new URL(request.url).pathname;
    if (!['GET', 'HEAD'].includes(request.method)) requireOperatorMutation(request, config);
    if (path === '/admin/v1/session') {
      if (request.method !== 'GET') return json(405, 'method_not_allowed');
      return Response.json({ version: 1, subject: principal.subject }, { headers: securityHeaders });
    }
    if (path === '/admin/v1/moderation' || path.startsWith('/admin/v1/moderation/') ||
        ['/admin/v1/reports', '/admin/v1/versions', '/admin/v1/alias-suggestions', '/admin/v1/recompute'].includes(path)) {
      const response = await operatorModeration(request, env, principal.subject, Math.floor((now?.getTime() ?? Date.now()) / 1000));
      for (const [name, value] of Object.entries(securityHeaders)) response.headers.set(name, value);
      return response;
    }
    if (path.startsWith('/admin/v1/dashboard/')) {
      const response = await operatorDashboard(request, env, Math.floor((now?.getTime() ?? Date.now()) / 1000));
      for (const [name, value] of Object.entries(securityHeaders)) response.headers.set(name, value);
      return response;
    }
    if (path === '/admin/v1' || path.startsWith('/admin/v1/')) return json(404, 'not_found');
    if (!['GET', 'HEAD'].includes(request.method)) return json(405, 'method_not_allowed');
    let asset: string;
    if (['/admin', '/admin/', '/admin/index.html'].includes(path)) asset = '/index.html';
    else if (/^\/admin\/assets\/[A-Za-z0-9_.-]+\.(?:js|css|woff2|png|svg)$/.test(path)) asset = path.slice('/admin'.length);
    else return json(404, 'not_found');
    if (!env.OPERATOR_ASSETS) return json(503, 'operator_assets_unavailable');
    const url = new URL(asset, config.origin);
    const response = await env.OPERATOR_ASSETS.fetch(new Request(url, { method: request.method }));
    if (response.status !== 200) return json(404, 'not_found');
    const headers = new Headers(response.headers);
    headers.delete('Set-Cookie');
    for (const [name, value] of Object.entries(securityHeaders)) headers.set(name, value);
    return new Response(response.body, { status: 200, headers });
  } catch (error) {
    if (error instanceof OperatorDenied) return json(error.status, error.code);
    return json(503, 'operator_verification_unavailable');
  }
}