export const confirmationPattern = /^[0-9a-f]{64}$/;

/** Bind a backend-held confirmation secret without retaining the secret in SQLite. */
export async function confirmationHash(secret: string): Promise<string> {
  if (!confirmationPattern.test(secret)) throw new Error('invalid_confirmation');
  const bytes = new TextEncoder().encode(`editarr-enrollment-confirmation-v1:${secret}`);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
}