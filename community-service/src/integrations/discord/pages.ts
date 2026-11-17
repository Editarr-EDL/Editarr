const icon = 'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAEKADAAQAAAABAAAAEAAAAAA0VXHyAAADWklEQVQ4ETVTXW8bRRQ9d2dtr7e248T5gDQkJLRykpIaJBC0YKn0S4iKiqi0aqFIoAgJqS/wwA/gGYm+IKG+VEhISIgHXiIQTwkJVKRV2pImwUmpIWmc2NSO11/s2vtxmU3S0cxqdGfOzjn3nkvYG/HnrsZbbuUYe1ZS6ImzakhPgQhgIjmVVqO27JrGd2pIWw3GI9PG9CeGD5U3AHX44yPUMq8zu8PMHuC5MupvCB4zFALvf35UVLcMVPMFKGogg2Bkwsl8cYMwfCUhGuWb7DpD7Lk7SPL/K6fnehQQHk5cGofW9xQt/JpHdTOPSv6eYjetLKK9L6miVjy+C3bcHco+JQZ5tgU9xDhz+Tw50U6E9UF0tpfRKACpV056Tmtj6O70nWMKu/aYXLuMPcnadcgzy2gLNPDu++OkhqPYXL2Pn69dhWfXcXB0AIlBHdtWXbLkMQWKuALXdiG1+tq5WUNHjHB+4j0qNNuwspzD159/gOHRGLJzk+CgifkbU1ifmQXF97+tyFx1+Jz9nLFjAdY2VB1Y7X4SUw8JhWoNn371LXJbBSTTp/Dn7V9QnJtjUnUmRcRJeWLc5WbdY9umtghw9tKblKlW+O7fS4i+eppaRhN8ex4jh9P46+YsjMUFJr1blkYVlEjm1V3aJrW3BXBx4iJVvB5U63HalxpDvbLCR8+l0fPyAcr+9DuMO78xtG6pXZGMdySzgDb0WXuE+NyHlylfUTAzu4bSgyW4KxkMxrvoYcnA4fojuvDiUWj9I1Qz/0OjVCLpD6JQoiGg9L7z7MnjnXb4aSwtWtjOLcCu3cczQz3oSI4g+8P3FAoF+bXXT5MS7kZGV0jIMjdyWwqr+i3JoF8vbqyfyq1VmUUC8a5+9PdG0D6WwvzkN7Cy67z+4BFmFpcRi0ap/K+B0mYJZrFEUsSXhFj6DFx3Ek7dRSBOySNvoevQAOZ/vM7mPxugWB9kqZltU3qkRUEtBMcywUIXrIbfkIY9FKFwYApi3wvSKV4oEmPWgmgWyyC9U4LVXW/6X88liZad4fhZnGMtcGKnmYC+gyT0a1C1tF8fkAAFNInwe2LvyuMm82xbhqfZUz6CdSvrn/pLyoEAelPyyQEZCkKIPaQ8eTxcSKfxGrD2hwxJ3wP/A8NhihyL/AqjAAAAAElFTkSuQmCC';

const styles = `:root{color-scheme:dark;color:#e6e9ef;background:#111318;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-synthesis:none;letter-spacing:0}
*{box-sizing:border-box}body{margin:0;min-height:100vh}main{display:grid;min-height:100vh;place-items:center;padding:24px;background:radial-gradient(circle at 50% 0,#253c62 0,#111318 48rem)}
.auth-panel{width:min(540px,100%);padding:32px;border:1px solid #3b4658;border-radius:14px;background:#191e27;box-shadow:0 24px 70px #0009}
.auth-brand{display:flex;align-items:center;justify-content:center;gap:11px;color:#f2f6ff;font-size:22px;font-weight:800}.auth-brand img{width:32px;height:32px;border-radius:8px}
.auth-heading{margin:30px 0 24px;text-align:center}h1{margin:0;color:#f7f9ff;font-size:28px;line-height:1.2}p{margin:9px 0 0;color:#aeb9ca;font-size:14px;line-height:1.5}
dl{margin:0 0 24px;border-block:1px solid #303641}dl>div{padding:16px 0}dl>div+div{border-top:1px solid #303641}dt{margin-bottom:7px;color:#a6b2c6;font-size:12px;font-weight:700}dd{margin:0;min-width:0}code{color:#dce4f2;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.65;overflow-wrap:anywhere}
button{width:100%;min-height:44px;padding:11px 13px;border:1px solid transparent;border-radius:7px;color:#06101f;background:#8dafef;font:inherit;font-size:14px;font-weight:700;cursor:pointer}button:hover{background:#afc8ff}button:focus-visible{outline:3px solid #80a7ff;outline-offset:3px}
.auth-success{padding:12px;border:1px solid #3d7768;border-radius:8px;color:#b7f0d8;background:#173a32;text-align:center}
@media(max-width:480px){main{padding:16px}.auth-panel{padding:24px 20px}h1{font-size:24px}}`;

function escapeHtml(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

export const discordAuthorizeUrl = 'https://discord.com/oauth2/authorize';

/** Render standalone broker pages with only a hash-authorized style block and embedded brand asset. */
export async function discordPage(input: { stateId: string; installationId: string; keyId: string; recovery?: boolean } | null,
  responseHeaders: Record<string, string>, enrollmentReference?: string): Promise<Response> {
  const title = input ? input.recovery ? 'Recover Editarr identity' : 'Connect Editarr' : 'Discord verified';
  const content = input ? `<div class="auth-heading"><h1>${title}</h1><p>${input.recovery ? 'Authorize replacement of this installation signing key. Its old copies will lose access immediately; other installations keep access. Return to Editarr for final confirmation.' : 'Connect this installation to your Discord account?'}</p></div><dl><div><dt>Installation</dt><dd><code>${escapeHtml(input.installationId)}</code></dd></div><div><dt>Signing key</dt><dd><code>${escapeHtml(input.keyId)}</code></dd></div></dl><form method="post" action="/v1/discord/confirm?state=${escapeHtml(input.stateId)}"><button type="submit">Continue with Discord</button></form>` :
    `<div class="auth-heading"><h1>Discord verified</h1></div><p class="auth-success">Discord membership verified. Return to Editarr to finish connecting.</p>${enrollmentReference && /^[0-9a-f]{64}$/.test(enrollmentReference) ? `<dl><div><dt>Enrollment reference</dt><dd><code>${escapeHtml(enrollmentReference)}</code></dd></div></dl>` : ''}`;
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(styles)));
  const hash = btoa(String.fromCharCode(...digest));
  const formAction = input ? `'self' ${discordAuthorizeUrl}` : "'none'";
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title><style>${styles}</style></head><body><main><section class="auth-panel" aria-label="${title}"><div class="auth-brand"><img src="data:image/png;base64,${icon}" alt="" width="32" height="32"><span>Editarr</span></div>${content}</section></main></body></html>`, {
    headers: { ...responseHeaders, 'Content-Type': 'text/html; charset=utf-8',
      'Content-Security-Policy': `default-src 'none'; style-src 'sha256-${hash}'; img-src data:; frame-ancestors 'none'; form-action ${formAction}; base-uri 'none'` },
  });
}
