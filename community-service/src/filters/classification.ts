import type { Cluster } from './clustering';
import type { MergePolicy } from './policy';
import { compareAscii, integerMedian, spreadOf } from './stats';
import type { FilterAction, RegistryEntry, Severity } from './types';

const SEVERITY_RANK: Record<Severity, number> = { low: 0, medium: 1, high: 2 };

export interface Classification {
  category: string;
  subcategory: string;
  severity: Severity;
  action: FilterAction;
  expression: string | null;
  startMs: number;
  endMs: number;
  spread: { startMs: number; endMs: number };
  tallies: { subcategory: Record<string, number>; severity: Record<string, number>; action: Record<string, number> };
}

/**
 * Plurality vote (§6.7): on a tie keep the incumbent, else the first member's
 * value in canonical order, else the ASCII-smallest tied value.
 */
export function pluralityVote<T extends string>(values: readonly T[], incumbent: T | null) {
  const counts = tally(values);
  const top = Math.max(...counts.values());
  const tied = [...counts].filter(([, count]) => count === top).map(([value]) => value);
  if (tied.length === 1) return { winner: tied[0], counts };
  if (incumbent !== null && tied.includes(incumbent)) return { winner: incumbent, counts };
  if (tied.includes(values[0])) return { winner: values[0], counts };
  return { winner: tied.sort(compareAscii)[0], counts };
}

/** Weighted severity vote (§6.7): highest total wins, ties go to the more severe value. */
export function severityVote(values: readonly Severity[], weights: MergePolicy['severityWeights']) {
  const totals = new Map<Severity, number>();
  for (const value of values) totals.set(value, (totals.get(value) ?? 0) + weights[value]);
  let winner: Severity | null = null;
  for (const [value, total] of totals) {
    const best = winner === null ? -Infinity : totals.get(winner)!;
    if (total > best || (total === best && SEVERITY_RANK[value] > SEVERITY_RANK[winner!])) winner = value;
  }
  return { winner: winner!, totals };
}

/** Case-insensitive grouping key of an expression: default upper- then lower-case mapping, then NFC (§6.7). */
export function expressionKey(expression: string) {
  return expression.toUpperCase().toLowerCase().normalize('NFC');
}

/** The most frequent value, ties going to `incumbent` when tied, else the code-unit-smallest value. */
function topValue(counts: ReadonlyMap<string, number>, incumbent: string | null) {
  const top = Math.max(...counts.values());
  const tied = [...counts].filter(([, count]) => count === top).map(([value]) => value);
  if (incumbent !== null && tied.includes(incumbent)) return incumbent;
  return tied.sort(compareAscii)[0];
}

/**
 * Expression vote (§6.7): the plurality case-insensitive group among members that carry an
 * expression wins, ties keeping the incumbent's group, then the smallest key. The display value
 * is the group's most common spelling, ties keeping the incumbent spelling, then the smallest one.
 * Null when no member carries an expression.
 */
export function expressionVote(values: readonly (string | null | undefined)[], incumbent: string | null) {
  const expressions = values.filter((value): value is string => typeof value === 'string');
  if (!expressions.length) return null;
  const groups = tally(expressions.map(expressionKey));
  const key = topValue(groups, incumbent === null ? null : expressionKey(incumbent));
  return topValue(tally(expressions.filter(value => expressionKey(value) === key)), incumbent);
}

/** Classify a cluster from its weight-1 members, which are in canonical order (§6.7, §6.4). */
export function classifyCluster(cluster: Cluster, incumbent: RegistryEntry | undefined, policy: Pick<MergePolicy, 'severityWeights'>): Classification {
  const entries = cluster.members.map(member => member.entry);
  const subcategory = pluralityVote(entries.map(entry => entry.subcategory!), incumbent?.incumbentSubcategory ?? null);
  const severity = severityVote(entries.map(entry => entry.severity!), policy.severityWeights);
  const action = pluralityVote(entries.map(entry => entry.action!), incumbent?.incumbentAction ?? null);
  const expression = expressionVote(entries.map(entry => entry.expression), incumbent?.incumbentExpression ?? null);
  const starts = cluster.members.map(member => member.alignedStartMs), ends = cluster.members.map(member => member.alignedEndMs);
  const startMs = integerMedian(starts), endMs = integerMedian(ends);
  return {
    category: cluster.category, subcategory: subcategory.winner, severity: severity.winner, action: action.winner, expression,
    startMs, endMs, spread: { startMs: spreadOf(starts, startMs), endMs: spreadOf(ends, endMs) },
    tallies: { subcategory: sortedRecord(subcategory.counts), severity: sortedRecord(severity.totals), action: sortedRecord(action.counts) },
  };
}

function tally<T extends string>(values: readonly T[]) {
  const counts = new Map<T, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

function sortedRecord(map: ReadonlyMap<string, number>) {
  return Object.fromEntries([...map].sort(([a], [b]) => compareAscii(a, b)));
}
