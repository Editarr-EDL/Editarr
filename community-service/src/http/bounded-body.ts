export class BodyLimitError extends Error {}

export async function readBoundedBytes(input: Request | Response, limit: number): Promise<Uint8Array> {
  if (!Number.isSafeInteger(limit) || limit < 0) throw new BodyLimitError('invalid_limit');
  const reader = input.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      length += chunk.value.length;
      if (length > limit) throw new BodyLimitError('body_too_large');
      chunks.push(chunk.value);
    }
  } finally {
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.length;
  }
  return body;
}

export async function readBoundedJson(input: Request | Response, limit: number): Promise<unknown> {
  const bytes = await readBoundedBytes(input, limit);
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}