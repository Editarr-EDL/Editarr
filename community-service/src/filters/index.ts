/** Public surface of the pure community master filter merge engine (contract §6). */
export { mergeVersion, compareSnapshot } from './merge';
export { DEFAULT_MASTER_POLICY, DEFAULT_MERGE_POLICY, type MasterPolicy, type MergePolicy } from './policy';
export { alignTime } from './alignment';
export { deriveFilterId } from './ids';
export { matchKeyIdentity } from './counted';
export { nextRevision, servedContent, snapshotFilter } from './revision';
export { integerMedian, wilson } from './stats';
export type * from './types';
