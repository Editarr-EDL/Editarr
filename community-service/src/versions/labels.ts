import type { SqlDatabase } from '../runtime/contracts';
import { normalize } from './normalize';

export const THEATRICAL = 'theatrical';

/** A resolved version label. Both null means "no version", which equals Theatrical (§3.4). */
export interface Label { aliasId: string | null; labelText: string | null }

/** A client or artifact label choice before resolution through approved spellings. */
export type LabelChoice = { aliasId: string } | { text: string | null } | null;

function effectiveAlias(label: Label): string | null {
  return label.aliasId ?? (label.labelText === null ? THEATRICAL : null);
}

/**
 * Label equality from §3.5 rule 3. Null and `theatrical` alias IDs are the same, and an
 * unaliased label with null text is "no version"; unaliased labels compare their text.
 */
export function labelsEqual(left: Label, right: Label): boolean {
  const a = effectiveAlias(left), b = effectiveAlias(right);
  if (a !== null || b !== null) return a === b;
  return left.labelText === right.labelText;
}

/** Look up the alias for one normalized approved spelling. */
export type SpellingLookup = (normalized: string) => Promise<string | null>;

/**
 * Resolve Other text: `labelText` is the normalized text (null when blank) and `aliasId`
 * is set when it equals an approved spelling.
 */
export async function resolveText(text: string | null, lookup: SpellingLookup): Promise<Label> {
  const normalized = normalize(text ?? '');
  if (!normalized) return { aliasId: null, labelText: null };
  return { aliasId: await lookup(normalized), labelText: normalized };
}

export function spellingLookup(db: SqlDatabase): SpellingLookup {
  return async normalized => (await db.prepare('SELECT alias_id FROM alias_spellings WHERE normalized = ?')
    .bind(normalized).first<{ alias_id: string }>())?.alias_id ?? null;
}

export async function aliasExists(db: SqlDatabase, aliasId: string): Promise<boolean> {
  return !!await db.prepare('SELECT 1 AS found FROM aliases WHERE alias_id = ?').bind(aliasId).first();
}

export interface AliasListing { aliasId: string; label: string; spellings: string[] }

/**
 * Approved aliases ordered with `theatrical` first, then by display label in ASCII order.
 * Spellings are normalized, ASCII-sorted, and omit the empty "no version" spelling.
 */
export async function listAliases(db: SqlDatabase): Promise<AliasListing[]> {
  const rows = await db.prepare(`SELECT a.alias_id, a.label, s.normalized FROM aliases a
    LEFT JOIN alias_spellings s ON s.alias_id = a.alias_id AND s.normalized != ''`)
    .all<{ alias_id: string; label: string; normalized: string | null }>();
  const aliases = new Map<string, AliasListing>();
  for (const row of rows.results) {
    const alias = aliases.get(row.alias_id) ?? { aliasId: row.alias_id, label: row.label, spellings: [] };
    if (row.normalized !== null) alias.spellings.push(row.normalized);
    aliases.set(row.alias_id, alias);
  }
  const ascii = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
  return [...aliases.values()].map(alias => ({ ...alias, spellings: alias.spellings.sort(ascii) }))
    .sort((a, b) => Number(b.aliasId === THEATRICAL) - Number(a.aliasId === THEATRICAL) ||
      ascii(a.label, b.label) || ascii(a.aliasId, b.aliasId));
}

/** Display label for a version: alias label, else `labelText`, else null (unlabeled candidate). */
export async function displayLabels(db: SqlDatabase): Promise<Map<string, string>> {
  const rows = await db.prepare('SELECT alias_id, label FROM aliases').all<{ alias_id: string; label: string }>();
  return new Map(rows.results.map(row => [row.alias_id, row.label]));
}
