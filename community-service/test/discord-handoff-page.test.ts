import { expect, it } from 'vitest';
import { discordPage } from '../src/integrations/discord/pages';

it('shows only the public enrollment reference after verification', async () => {
  const reference = 'b'.repeat(64);
  const response = await discordPage(null, { 'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer' }, reference);
  const html = await response.text();
  expect(html).toContain('Enrollment reference');
  expect(html).toContain(`<code>${reference}</code>`);
  expect(html).not.toContain('confirmationSecret');
  expect(html).not.toContain('access_token');
  expect(response.headers.get('content-security-policy')).toContain("default-src 'none'");
  expect(response.headers.get('cache-control')).toBe('no-store');
});

it('does not render an invalid reference as markup', async () => {
  const response = await discordPage(null, {}, '<script>alert(1)</script>');
  expect(await response.text()).not.toContain('<script>');
});