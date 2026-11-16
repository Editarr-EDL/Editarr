import { resolve } from 'node:path';
import { initializeDatabase, openDatabase, upgradeDatabase, verifiedBackup } from './schema.mjs';

async function main() {
  process.umask(0o077);
  const [command, ...arguments_] = process.argv.slice(2);
  const usage = 'usage: manage.mjs init --confirm | backup|upgrade --confirm --destination NEW_FILE | retention --dry-run';
  if (command === '--help' && !arguments_.length) { console.log(usage); return; }
  if (command === 'retention') {
    if (arguments_.length !== 1 || arguments_[0] !== '--dry-run') throw new Error(usage);
    const path = process.env.COMMUNITY_DATABASE ?? '/data/community.sqlite';
    const { database, version } = openDatabase(path, undefined, false, { readOnly: true });
    try {
      const { previewRetention } = await import('./service.mjs');
      console.log(JSON.stringify({ ...await previewRetention(database, Date.now()), schemaVersion: version }));
    } finally { database.close(); }
    return;
  }
  if (!['init', 'backup', 'upgrade'].includes(command) || arguments_[0] !== '--confirm' ||
      (command === 'init' ? arguments_.length !== 1 : arguments_.length !== 3 || arguments_[1] !== '--destination')) {
    throw new Error(usage);
  }
  const path = process.env.COMMUNITY_DATABASE ?? '/data/community.sqlite';
  if (command === 'init') initializeDatabase(path);
  else {
    const destination = resolve(arguments_[2]);
    if (command === 'upgrade') await upgradeDatabase(path, destination);
    else {
      const { database, inventory } = openDatabase(path);
      try { await verifiedBackup(database, destination, inventory); }
      finally { database.close(); }
    }
  }
  console.log(`community_${command}_complete`);
}

main().catch(error => {
  const code = error.message;
  console.error(code.startsWith('usage:') ? code : 'community_management_refused');
  process.exitCode = 1;
});