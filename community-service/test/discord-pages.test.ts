import { describe, expect, it } from 'vitest';
import { discordPage } from '../src/integrations/discord/pages';

describe('Editarr hosted Discord pages', () => {
  it('authorizes only the exact stylesheet while preserving script and form restrictions', async () => {
    const response = await discordPage({ stateId: 'a'.repeat(64),
      installationId: crypto.randomUUID(), keyId: `sha256:${'b'.repeat(64)}` },
    { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY' });
    const markup = await response.text();
    const css = markup.match(/<style>([\s\S]*?)<\/style>/)![1];
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(css)));
    const csp = response.headers.get('content-security-policy')!;
    expect(csp).toContain(`style-src 'sha256-${btoa(String.fromCharCode(...digest))}'`);
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain('unsafe-inline');
    expect(markup).not.toContain('<script');
    expect(markup).toContain('data:image/png;base64,');
    expect(markup).toContain('class="auth-panel"');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('escapes displayed identities and form targets', async () => {
    const response = await discordPage({ stateId: '" onfocus="alert(1)',
      installationId: '<script>bad</script>', keyId: 'a&b' }, {});
    const markup = await response.text();
    expect(markup).toContain('&lt;script&gt;bad&lt;/script&gt;');
    expect(markup).toContain('a&amp;b');
    expect(markup).toContain('&quot; onfocus=&quot;');
    expect(markup).not.toContain('<script>');
  });

  it('uses the same branded layout for verified handoffs without claiming enrollment', async () => {
    const markup = await (await discordPage(null, {})).text();
    expect(markup).toContain('class="auth-panel"');
    expect(markup).toContain('Return to Editarr to finish connecting.');
    expect(markup).not.toContain('<form');
  });
});