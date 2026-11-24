import { parseTree, type Node, type ParseError } from 'jsonc-parser';

/** Bytes are not strict, bounded UTF-8 JSON, or not a strict single-member gzip of it. */
export class StrictJsonError extends Error {}

export interface JsonBounds {
  maxBytes: number; maxDepth?: number; maxStringBytes?: number;
  /** Paths whose numbers may have a fraction or exponent; every other number must be an integer literal. */
  fractionPaths?: readonly string[];
}
const encoder = new TextEncoder();

/**
 * Enforce unique keys, container depth (root container = 1), string byte length, safe
 * integers, and integer literals outside `fractionPaths` (paths join keys with `.`, arrays as `[]`).
 */
function inspect(node: Node, bounds: JsonBounds, depth: number, source: string, path: string) {
  if (node.type === 'string') {
    if (bounds.maxStringBytes !== undefined && encoder.encode(node.value as string).length > bounds.maxStringBytes)
      throw new StrictJsonError('JSON string exceeds the byte limit');
    return;
  }
  if (node.type === 'number') {
    const value = node.value as number;
    if (!Number.isFinite(value) || (Number.isInteger(value) && Math.abs(value) > Number.MAX_SAFE_INTEGER))
      throw new StrictJsonError('JSON number is not a finite safe value');
    if (!/^-?(?:0|[1-9][0-9]*)$/.test(source.slice(node.offset, node.offset + node.length)) &&
        !bounds.fractionPaths?.includes(path)) throw new StrictJsonError('JSON integer has a fraction or exponent');
    return;
  }
  if (node.type !== 'object' && node.type !== 'array') return;
  if (bounds.maxDepth !== undefined && depth > bounds.maxDepth) throw new StrictJsonError('JSON nesting exceeds the depth limit');
  if (node.type === 'array') {
    for (const child of node.children ?? []) inspect(child, bounds, depth + 1, source, `${path}[]`);
    return;
  }
  const keys = new Set<string>();
  for (const property of node.children ?? []) {
    const [key, value] = property.children ?? [];
    if (!key || !value) throw new StrictJsonError('Invalid JSON property');
    if (keys.has(key.value as string)) throw new StrictJsonError('Duplicate JSON field');
    keys.add(key.value as string);
    inspect(key, bounds, depth + 1, source, path);
    inspect(value, bounds, depth + 1, source, path ? `${path}.${key.value as string}` : key.value as string);
  }
}

/**
 * Decode bounded UTF-8 JSON with no BOM, duplicate keys, non-finite numbers or unsafe
 * integers, checking depth and string bounds before the value is materialized.
 */
export function parseStrictJson(bytes: Uint8Array, bounds: JsonBounds): unknown {
  if (bytes.byteLength > bounds.maxBytes) throw new StrictJsonError('JSON exceeds the byte limit');
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) throw new StrictJsonError('JSON must not start with a BOM');
  let source: string;
  try { source = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new StrictJsonError('Invalid UTF-8'); }
  const errors: ParseError[] = [];
  const tree = parseTree(source, errors, { disallowComments: true, allowTrailingComma: false, allowEmptyContent: false });
  if (!tree || errors.length) throw new StrictJsonError('Invalid JSON');
  inspect(tree, bounds, 1, source, '');
  try { return JSON.parse(source) as unknown; }
  catch { throw new StrictJsonError('Invalid JSON'); }
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * Expand exactly one gzip member with no `FEXTRA`, `FNAME`, `FCOMMENT` or `FHCRC`, within
 * byte limits, and check its CRC-32 and size trailer (§5.1).
 */
export async function gunzipSingleMember(bytes: Uint8Array, maxCompressed: number, maxExpanded: number): Promise<Uint8Array> {
  if (bytes.byteLength > maxCompressed) throw new StrictJsonError('Compressed bytes exceed the limit');
  if (bytes.byteLength < 18 || bytes[0] !== 0x1f || bytes[1] !== 0x8b || bytes[2] !== 0x08)
    throw new StrictJsonError('Not a deflate gzip member');
  if (bytes[3] & ~0x01) throw new StrictJsonError('Gzip header flags are not allowed');
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    const reader = new Blob([bytes.subarray(10, bytes.byteLength - 8) as BlobPart]).stream()
      .pipeThrough(new DecompressionStream('deflate-raw')).getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > maxExpanded) throw new StrictJsonError('Expanded bytes exceed the limit');
        chunks.push(value);
      }
    } finally { await reader.cancel().catch(() => undefined); }
  } catch (error) {
    if (error instanceof StrictJsonError) throw error;
    throw new StrictJsonError('Gzip must contain exactly one complete member');
  }
  const expanded = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { expanded.set(chunk, offset); offset += chunk.byteLength; }
  const trailer = new DataView(bytes.buffer, bytes.byteOffset + bytes.byteLength - 8, 8);
  if (trailer.getUint32(0, true) !== crc32(expanded) || trailer.getUint32(4, true) !== (length >>> 0))
    throw new StrictJsonError('Gzip trailer does not match the expanded bytes');
  return expanded;
}
