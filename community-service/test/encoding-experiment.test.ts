import { createFileRegistry, fromBinary, fromJson, toBinary, toJson,
  type DescMessage, type JsonValue } from '@bufbuild/protobuf';
import { FileDescriptorProtoSchema } from '@bufbuild/protobuf/wkt';
import { env } from './helpers/environment';
import { describe, expect, it } from 'vitest';

import { fixtures, type Artifact } from './encoding-fixtures';
import golden from './fixtures/encoding-vectors.json';

const descriptor = Uint8Array.from(atob(golden.descriptor_base64), (char) => char.charCodeAt(0));
const registry = createFileRegistry(fromBinary(FileDescriptorProtoSchema, descriptor),
  () => undefined);
const artifactType = registry.getMessage('bench.Artifact')!;
const summaryType = registry.getMessage('bench.Summary')!;

function summary(artifact: Artifact) {
  const { events, ...fields } = artifact;
  return { ...fields, event_count: events.length };
}

function encode(type: DescMessage, value: object): Uint8Array {
  return toBinary(type, fromJson(type, value as JsonValue));
}

function decode(type: DescMessage, raw: Uint8Array): Record<string, unknown> {
  const result = toJson(type, fromBinary(type, raw), {
    alwaysEmitImplicit: true, useProtoFieldName: true,
  }) as Record<string, unknown>;
  result.parent_digest ??= null;
  for (const stage of result.stages as Record<string, unknown>[]) {
    for (const field of ['model_id', 'model_revision', 'build_revision', 'model_digest',
      'quantization', 'sampling_seconds']) stage[field] ??= null;
  }
  return result;
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function gzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function ungzip(bytes: Uint8Array): Promise<Uint8Array> {
  if (bytes.byteLength > 64 * 1024) throw new Error('Compressed upload exceeds test bound');
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream('gzip'));
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 512 * 1024) throw new Error('Expanded upload exceeds test bound');
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function timeOperation(operation: () => unknown): { median_ms: number; p95_ms: number } {
  const samples: number[] = [];
  for (let attempt = 0; attempt < 20; attempt++) {
    const started = performance.now();
    operation();
    samples.push(performance.now() - started);
  }
  samples.sort((left, right) => left - right);
  return { median_ms: samples[9], p95_ms: samples[18] };
}

describe('experimental Python/Node service Protobuf interchange', () => {
  for (const [name, fixture] of Object.entries(fixtures())) {
    for (const [surface, type, value] of [
      ['r2_body', artifactType, fixture],
      ['d1_summary', summaryType, summary(fixture)],
    ] as const) {
      it(`${name} ${surface} has Python wire bytes and nullable semantic parity`, async () => {
        const bytes = encode(type, value);
        expect(decode(type, bytes)).toEqual(value);
        const expected = golden.vectors[name as keyof typeof golden.vectors][surface].protobuf;
        expect({ bytes: bytes.byteLength, sha256: await sha256(bytes) }).toEqual(expected);
      });
    }
  }

  it('bounds both local Node service candidates and checks signed bytes before storage', async () => {
    const fixture = fixtures().large;
    const jsonBytes = new TextEncoder().encode(JSON.stringify(fixture));
    const protoBytes = encode(artifactType, fixture);
    console.info(JSON.stringify({ benchmark: 'local-Node-codec-wall-only',
      json_encode: timeOperation(() => JSON.stringify(fixture)),
      json_decode: timeOperation(() => JSON.parse(new TextDecoder().decode(jsonBytes))),
      proto_encode: timeOperation(() => encode(artifactType, fixture)),
      proto_decode: timeOperation(() => decode(artifactType, protoBytes)),
    }));
    const signingKey = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']);
    await env.CATALOG_DB.prepare('CREATE TABLE IF NOT EXISTS bench_summary '
      + '(digest TEXT PRIMARY KEY, work_id TEXT NOT NULL, kind TEXT NOT NULL, '
      + 'json_summary TEXT, proto_summary BLOB, artifact BLOB)').run();
    const d1 = summary(fixture);
    const jsonSummary = JSON.stringify(d1);
    const protoSummary = encode(summaryType, d1);
    for (const [format, payload] of [
      ['protobuf', protoBytes],
      ['json', jsonBytes],
    ] as const) {
      const compressed = await gzip(payload);
      const signature = await crypto.subtle.sign('Ed25519', signingKey.privateKey,
        compressed as BufferSource);
      const started = performance.now();
      const expanded = await ungzip(compressed);
      const digest = await sha256(compressed);
      const parsed = format === 'protobuf' ? decode(artifactType, expanded)
        : JSON.parse(new TextDecoder().decode(expanded)) as Record<string, unknown>;
      const valid = parsed.schema_version === 'experiment-1'
        && (parsed.events as unknown[]).length === 2500
        && await crypto.subtle.verify('Ed25519', signingKey.publicKey, signature,
          compressed as BufferSource);
      expect(valid).toBe(true);
      await env.CATALOG_DB.prepare('INSERT OR REPLACE INTO bench_summary VALUES (?, ?, ?, ?, ?, ?)')
        .bind(digest, parsed.work_id, parsed.kind, jsonSummary, protoSummary, compressed).run();
      expect((await env.CATALOG_DB.prepare('SELECT work_id FROM bench_summary WHERE digest = ?')
        .bind(digest).first<{ work_id: string }>())?.work_id).toBe('movie:synthetic');
      expect(await env.CATALOG_DB.prepare('SELECT length(artifact) AS size FROM bench_summary WHERE digest = ?')
        .bind(digest).first()).toEqual({ size: compressed.byteLength });
      console.info(JSON.stringify({ benchmark: 'local-Node-only', format,
        compressed: compressed.byteLength, expanded: expanded.byteLength,
        json_summary: jsonSummary.length, proto_summary: protoSummary.length,
        elapsed_wall_ms: performance.now() - started }));
    }
    await expect(ungzip(await gzip(new Uint8Array(513 * 1024)))).rejects.toThrow('Expanded');
    await expect(ungzip(new Uint8Array(64 * 1024 + 1))).rejects.toThrow('Compressed');
    let seed = 12345;
    const noisy = { ...fixture, events: fixture.events.map((event) => ({ ...event,
      note: Array.from({ length: 120 }, () => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return String.fromCharCode(33 + (seed % 90));
      }).join(''),
    })) };
    const noisyBytes = new TextEncoder().encode(JSON.stringify(noisy));
    const noisyCompressed = await gzip(noisyBytes);
    expect(noisy.events).toHaveLength(2500);
    expect(noisyCompressed.byteLength).toBeGreaterThan(64 * 1024);
    await expect(ungzip(noisyCompressed)).rejects.toThrow('Compressed');
  });
});