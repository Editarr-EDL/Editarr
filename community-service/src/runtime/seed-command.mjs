import { lstatSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { readSeedSettings } from './environment.mjs';
import { openDatabase, verifiedBackup } from './schema.mjs';

export async function seedExternal(backupFile, service, bundleFiles) {
  const url = new URL(service);
  if (url.protocol !== 'https:' || url.origin !== service) throw new Error('invalid_seed_origin');
  const config = readSeedSettings(process.env.COMMUNITY_CONFIG_FILE);
  if (config.PUBLIC_ORIGIN && config.PUBLIC_ORIGIN !== service) throw new Error('invalid_seed_origin');
  const { importExternalSeed, seedOptions } = await import('./seed.mjs');
  const options = seedOptions(service, config.MASTER_POLICY);
  const bundles = bundleFiles.map(file => {
    const stat = lstatSync(file);
    if (!stat.isFile() || stat.size > 100_000_000) throw new Error('invalid_seed_file');
    return JSON.parse(readFileSync(file, 'utf8'));
  });
  const { database, inventory } = openDatabase(process.env.COMMUNITY_DATABASE ?? '/data/community.sqlite');
  try {
    await verifiedBackup(database, resolve(backupFile), inventory);
    for (const bundle of bundles) console.log(JSON.stringify(await importExternalSeed(database, bundle, options)));
  } finally {
    database.close();
  }
}
