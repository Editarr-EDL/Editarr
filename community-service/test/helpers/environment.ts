import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll } from 'vitest';
import type { SqlDatabase } from '../../src/runtime/contracts';
import { SqliteDatabase } from '../../src/runtime/sqlite.mjs';
import { operatorAssets } from '../../src/runtime/http.mjs';
import { tunnelActor } from '../../src/runtime/environment.mjs';

const directory = mkdtempSync(join(tmpdir(), 'community-test-'));
const database = new SqliteDatabase(join(directory, 'catalog.sqlite'));
database.lockWriter();

export interface FixtureDatabase extends SqlDatabase {
  exec(sql: string): Promise<{ count: number; duration: number }>;
}

export const env: { APP_ENV: string; CATALOG_DB: FixtureDatabase; OPERATOR_ASSETS: ReturnType<typeof operatorAssets>;
  TRUSTED_ACTOR_KEY: typeof tunnelActor } = {
  APP_ENV: 'local',
  TRUSTED_ACTOR_KEY: tunnelActor,
  CATALOG_DB: Object.assign(database, {
    async exec(sql: string) {
      database.connection.exec(sql);
      return { count: 1, duration: 0 };
    },
  }),
  OPERATOR_ASSETS: operatorAssets(new URL('../../../web/dist-operator/', import.meta.url)),
};

afterAll(() => {
  database.close();
  rmSync(directory, { recursive: true, force: true });
});