import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readArchive } from '../../scripts/backup/archive.mjs';

function archive(entries) {
  const blocks = entries.flatMap(([name, value, type = '0', target = '']) => {
    const header = Buffer.alloc(512);
    header.write(name); header.write('0000600\0', 100); header.write('0000000\0', 108);
    header.write('0000000\0', 116); header.write(value.length.toString(8).padStart(11, '0') + '\0', 124);
    header.write('00000000000\0', 136); header.fill(32, 148, 156);
    header.write(type, 156); header.write(target, 157); header.write('ustar\0', 257); header.write('00', 263);
    header.write(header.reduce((sum, item) => sum + item, 0).toString(8).padStart(6, '0') + '\0 ', 148);
    const data = Buffer.alloc(Math.ceil(value.length / 512) * 512); data.write(value);
    return [header, data];
  });
  return Buffer.concat([...blocks, Buffer.alloc(1024)]);
}

test('strict archive reader accepts only complete bounded regular members', () => {
  const valid = archive([['manifest.json', '{}'], ['database.sql', 'SELECT 1;']]);
  const limits = { 'manifest.json': 100, 'database.sql': 100 };
  assert.equal(readArchive(valid, limits).get('database.sql').toString(), 'SELECT 1;');
  for (const bytes of [valid.subarray(0, valid.length - 512), Buffer.concat([valid, Buffer.from('extra')]),
    archive([['manifest.json', '{}']]), archive([['manifest.json', '{}'], ['manifest.json', '{}']]),
    archive([['../database.sql', 'x'], ['manifest.json', '{}']]),
    archive([['database.sql', 'x', '2', '/tmp/target'], ['manifest.json', '{}']]),
    archive([['database.sql', 'x', '1', 'manifest.json'], ['manifest.json', '{}']]),
    archive([['database.sql', 'x', 'x'], ['manifest.json', '{}']])])
    assert.throws(() => readArchive(bytes, limits), /restore_archive_invalid/);
  assert.throws(() => readArchive(valid, { ...limits, 'database.sql': 1 }), /restore_archive_invalid/);
  valid[0] ^= 1;
  assert.throws(() => readArchive(valid, limits), /restore_archive_invalid/);
});

test('legacy macOS metadata is inert and cannot override paths, sizes or link targets', () => {
  const record = (key, value) => {
    const body = ` ${key}=${value}\n`;
    let length = body.length + 1;
    while (length !== body.length + String(length).length) length = body.length + String(length).length;
    return `${length}${body}`;
  };
  const limits = { 'manifest.json': 100, 'database.sql': 100 };
  const entries = [['._manifest.json', 'inert AppleDouble'],
    ['PaxHeader/manifest.json', record('mtime', '1.123'), 'x'],
    ['manifest.json', '{}'], ['database.sql', 'SELECT 1;']];
  assert.equal(readArchive(archive(entries), limits).size, 2);
  for (const key of ['path', 'linkpath', 'size', 'GNU.sparse.name', 'unknown']) {
    const altered = [...entries]; altered[1] = ['PaxHeader/manifest.json', record(key, '../../outside'), 'x'];
    assert.throws(() => readArchive(archive(altered), limits), /restore_archive_invalid/);
  }
});