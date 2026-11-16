type Grant = { stateId: string; installationId: string; keyId: string; privateKey: CryptoKey };
export type SigningOptions = {
  privateKey?: CryptoKey; body?: string; url?: string; expires?: number; operationId?: string;
  method?: 'POST' | 'PATCH'; nonce?: string;
};

export async function signedEnrollment(grant: Grant, now: number, target: string,
  options: SigningOptions = {}): Promise<Request> {
  const body = options.body ?? JSON.stringify({ version: 1, stateId: grant.stateId,
    installationId: grant.installationId });
  const url = options.url ?? target;
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body)));
  const contentDigest = `sha-256=:${btoa(String.fromCharCode(...digest))}:`;
  const operationId = options.operationId ?? grant.stateId;
  const signatureInput = 'editarr=("@method" "@scheme" "@authority" "@path" "@query" ' +
    '"content-digest" "content-type" "idempotency-key");' +
    `created=${now / 1000 - 10};expires=${options.expires ?? now / 1000 + 50};` +
    `nonce="${options.nonce ?? 'AQIDBAUGBwgJCgsMDQ4PEA'}";keyid="${grant.keyId}";alg="ed25519";tag="editarr-community-request-v1"`;
  const parsed = new URL(url);
  const base = `"@method": ${options.method ?? 'POST'}\n"@scheme": https\n"@authority": ${parsed.host}\n` +
    `"@path": ${parsed.pathname}\n"@query": ${parsed.search || '?'}\n` +
    `"content-digest": ${contentDigest}\n"content-type": application/json\n` +
    `"idempotency-key": ${operationId}\n"@signature-params": ${signatureInput.slice(8)}`;
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519',
    options.privateKey ?? grant.privateKey, new TextEncoder().encode(base)));
  return new Request(url, { method: options.method ?? 'POST', body, headers: { 'content-type': 'application/json',
    'content-digest': contentDigest, 'idempotency-key': operationId, 'signature-input': signatureInput,
    signature: `editarr=:${btoa(String.fromCharCode(...signature))}:` } });
}