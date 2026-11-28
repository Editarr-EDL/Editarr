import type { SqliteDatabase } from './sqlite.mjs';

type Inventory = { name: string; sql: string }[];
export const migrationsDirectory: URL;
export const SCHEMA_LINEAGE: string;
export const REPORTED_REFUSALS: Set<string>;
export function initializeDatabase(path: string, root?: URL): void;
export function openDatabase(path: string, root?: URL, allowUpgrade?: boolean, options?: { readOnly?: boolean }):
  { database: SqliteDatabase; inventory: Inventory; version: number };
export function verifySnapshot(path: string, options?: { integrity?: boolean; root?: URL }): { version: number; current: number };
export function verifiedBackup(database: SqliteDatabase, destination: string, inventory: Inventory): Promise<void>;
export function upgradeDatabase(path: string, destination: string, root?: URL): Promise<void>;
