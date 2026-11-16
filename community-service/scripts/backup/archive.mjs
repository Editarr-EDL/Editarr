import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { protectedInput } from './files.mjs';
import { CliError } from '../operator/transport.mjs';

const decoder = new TextDecoder('utf-8', { fatal: true });
const text = bytes => decoder.decode(bytes).replace(/\0.*$/s, '');
const octal = bytes => {
  const value = text(bytes).trim();
  if (!/^[0-7]+$/.test(value)) throw new Error();
  const number = Number.parseInt(value, 8);
  if (!Number.isSafeInteger(number)) throw new Error();
  return number;
};

function inertPax(bytes) {
  let offset = 0;
  const keys = new Set();
  while (offset < bytes.length) {
    const space = bytes.indexOf(32, offset);
    if (space < offset || space - offset > 5) throw new Error();
    const digits = decoder.decode(bytes.subarray(offset, space));
    if (!/^[1-9][0-9]*$/.test(digits)) throw new Error();
    const length = Number(digits), end = offset + length;
    if (end > bytes.length || end <= space + 1 || bytes[end - 1] !== 10) throw new Error();
    const record = bytes.subarray(space + 1, end - 1);
    const equals = record.indexOf(61);
    if (equals < 1) throw new Error();
    const key = decoder.decode(record.subarray(0, equals));
    if (keys.has(key) || !/^(mtime|atime|ctime|SCHILY\.fflags|(LIBARCHIVE|SCHILY)\.xattr\.com\.apple\.(provenance|quarantine))$/.test(key))
      throw new Error();
    keys.add(key); offset = end;
  }
}

export function readArchive(bytes, limits) {
  try {
    const members = new Map();
    const seen = new Set();
    let offset = 0;
    while (offset + 512 <= bytes.length) {
      const header = bytes.subarray(offset, offset + 512);
      if (header.every(value => value === 0)) {
        if (bytes.length - offset < 1024 || bytes.length % 512 ||
            !bytes.subarray(offset).every(value => value === 0) ||
            members.size !== Object.keys(limits).length) throw new Error();
        return members;
      }
      const checksum = header.reduce((sum, value, index) => sum + (index >= 148 && index < 156 ? 32 : value), 0);
      const name = text(header.subarray(0, 100));
      const size = octal(header.subarray(124, 136));
        const apple = name.startsWith('._') && Object.hasOwn(limits, name.slice(2));
        const pax = name.startsWith('PaxHeader/') && Object.hasOwn(limits, name.slice(10));
      if (octal(header.subarray(148, 156)) !== checksum ||
          text(header.subarray(257, 263)) !== 'ustar' ||
          !(pax ? header[156] === 120 : [0, 48].includes(header[156])) || text(header.subarray(157, 257)) ||
          text(header.subarray(345, 500)) || (!Object.hasOwn(limits, name) && !apple && !pax) ||
          seen.has(name) || size < 1 || size > (apple || pax ? 4096 : limits[name])) throw new Error();
      offset += 512;
      const padded = Math.ceil(size / 512) * 512;
      if (offset + padded > bytes.length || !bytes.subarray(offset + size, offset + padded).every(value => value === 0))
        throw new Error();
      seen.add(name);
      const data = bytes.subarray(offset, offset + size);
      if (pax) inertPax(data);
      else if (!apple) members.set(name, data);
      offset += padded;
    }
    throw new Error();
  } catch { throw new CliError('restore_archive_invalid'); }
}

export async function decryptArchive(path, identity, limits, maximum, expected) {
  const ciphertext = await protectedInput(path, maximum);
  if (expected && (ciphertext.length !== expected.bytes ||
      createHash('sha256').update(ciphertext).digest('hex') !== expected.sha256))
    throw new CliError('restore_source_ciphertext_mismatch');
  const key = await protectedInput(identity);
  const child = spawn('age', ['--decrypt', '--identity', '/dev/fd/3'],
    { stdio: ['pipe', 'pipe', 'pipe', 'pipe'], timeout: 120000, env: { PATH: process.env.PATH } });
  const complete = new Promise((resolve, reject) => {
    child.on('error', () => reject(new CliError('restore_decryption_failed')));
    child.on('close', code => code === 0 ? resolve() : reject(new CliError('restore_decryption_failed')));
  });
  complete.catch(() => {});
  child.stderr.resume();
  child.stdin.on('error', () => {}); child.stdio[3].on('error', () => {});
  child.stdin.end(ciphertext); child.stdio[3].end(key);
  try {
    const chunks = [];
    let length = 0;
    const ceiling = Object.values(limits).reduce((sum, value) => sum + Math.ceil(value / 512) * 512 + 9728, 10240);
    for await (const chunk of child.stdout) {
      length += chunk.length;
      if (length > ceiling) throw new CliError('restore_archive_too_large');
      chunks.push(chunk);
    }
    await complete;
    return readArchive(Buffer.concat(chunks, length), limits);
  } finally { child.kill(); await complete.catch(() => {}); }
}