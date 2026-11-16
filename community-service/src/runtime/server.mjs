import service, { validateEnvironment } from './service.mjs';
import { readConfiguration, tunnelActor } from './environment.mjs';
import { openDatabase } from './schema.mjs';
import { createHttpServer, operatorAssets } from './http.mjs';
import { startMaintenance } from './maintenance.mjs';

async function main() {
  process.umask(0o077);
  const configuration = readConfiguration(process.env.COMMUNITY_CONFIG_FILE, process.env.COMMUNITY_SECRETS_FILE);
  const databasePath = process.env.COMMUNITY_DATABASE ?? '/data/community.sqlite';
  const { database } = openDatabase(databasePath);
  let server, maintenance;
  let closing = false;
  const active = new Set();
  try {
    const env = { ...configuration.env, CATALOG_DB: database,
      OPERATOR_ASSETS: operatorAssets(new URL('./assets/', import.meta.url)),
      TRUSTED_ACTOR_KEY: configuration.trustedTunnel ? tunnelActor : () => null };
    if (configuration.enabled) await validateEnvironment(env);
    server = createHttpServer(async request => {
      if (closing) return Response.json({ error: { code: 'service_unavailable' } }, { status: 503 });
      const work = Promise.resolve().then(() => service.fetch(request, env));
      active.add(work);
      try { return await work; }
      finally { active.delete(work); }
    }, { ...configuration, ready: () => !closing });
    const port = Number(process.env.PORT ?? 8787);
    if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('invalid_listen_port');
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, process.env.HOST ?? '127.0.0.1', resolve);
    });
    maintenance = startMaintenance(service, env, configuration.enabled);
    console.log(`community_listening port=${port} enabled=${configuration.enabled}`);
    const shutdown = () => {
      if (closing) return;
      closing = true;
      const maintenanceDone = maintenance?.stop();
      const deadline = setTimeout(() => server.closeAllConnections(), 25000);
      deadline.unref();
      server.close(async () => {
        await Promise.allSettled([...active, maintenanceDone]);
        clearTimeout(deadline);
        database.close();
      });
    };
    process.once('SIGTERM', shutdown);
    process.once('SIGINT', shutdown);
  } catch (error) {
    await maintenance?.stop();
    server?.close();
    database.close();
    throw error;
  }
}

main().catch(() => { console.error('community_startup_refused'); process.exitCode = 1; });