import type {
  AcceptedCreditsProvenance,
  AcceptedCreditsReference,
  NewManualReviewFilter,
  ReviewFilter,
  ReviewMcfDocument,
  McfMetadataSeedRequest,
  ReviewRetimeResult,
  ReviewCommunityMembership,
  ReviewScanMembership,
  ReviewSynchronization,
  SavedReview,
} from "../api/api-review-types";
import type { CreditsAcceptanceResult } from "../api/api-credits-types";

interface ReviewContent {
  name: string;
  memberships: ReviewScanMembership[];
  filters: ReviewFilter[];
  filterBases: Record<string, { base: ReviewFilter; effective: ReviewFilter }>;
  mcfDocument: ReviewMcfDocument;
  synchronization: ReviewSynchronization;
  acceptedCredits: AcceptedCreditsReference | AcceptedCreditsProvenance | null;
  mcfMetadataSeed: McfMetadataSeedRequest | null;
  newManualFilters: NewManualReviewFilter[];
  communityMemberships: ReviewCommunityMembership[];
}

export interface ReviewDraft extends ReviewContent {
  /** Saved review UUID, or a fresh browser-session UUID for an unsaved draft. */
  id: string;
  revision: number;
  policy_owner_id: number | null;
  policy_revision: number;
  contentRevision: number;
  edlContentRevision: number;
  mcfContentRevision: number;
  baseline: string;
  baselineState: ReviewContent;
  removedSnapshots: Record<string, ReviewFilter[]>;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

/** Drop derived, read-only provenance so it never marks a draft dirty. */
function comparableFilter({ label_edited: _derived, community: _community, ...filter }: ReviewFilter): Omit<ReviewFilter, "label_edited" | "community"> {
  return filter;
}

function contentState(draft: ReviewContent): string {
  return JSON.stringify({
    name: draft.name,
    memberships: draft.memberships,
    filters: draft.filters.map(comparableFilter),
    filterBases: Object.fromEntries(Object.entries(draft.filterBases).map(([id, pair]) => [
      id, { base: comparableFilter(pair.base), effective: comparableFilter(pair.effective) },
    ])),
    mcfDocument: draft.mcfDocument,
    synchronization: draft.synchronization,
    acceptedCredits: draft.acceptedCredits,
    mcfMetadataSeed: draft.mcfMetadataSeed,
    newManualFilters: draft.newManualFilters,
    communityMemberships: draft.communityMemberships,
  });
}

function stableId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function filterBases(filters: ReviewFilter[], baseFilters?: ReviewFilter[]): ReviewContent["filterBases"] {
  const bases = new Map(baseFilters?.map(filter => [filter.id, filter]));
  return Object.fromEntries(filters.map(filter => [filter.id, {
    effective: clone(filter), base: clone(bases.get(filter.id) ?? filter),
  }]));
}

export function reviewDraftFilters(draft: ReviewDraft): ReviewFilter[] {
  return draft.filters.map(filter => {
    const pair = draft.filterBases[filter.id];
    if (!pair) return filter;
    return {
      ...filter,
      selected: filter.selected === pair.effective.selected ? pair.base.selected : filter.selected,
      severity: filter.severity === pair.effective.severity ? pair.base.severity : filter.severity,
    };
  });
}

/**
 * Clone server filters, keeping read-only `label_edited` from the matching previous filter.
 * Write responses (defaults, retime, credits) omit the flag; it carries over only while the
 * label is unchanged, and a value the response does include wins.
 */
function withLabelProvenance(next: ReviewFilter[], previous: ReviewFilter[]): ReviewFilter[] {
  const previousById = new Map(previous.map((filter) => [filter.id, filter]));
  return clone(next).map((filter) => {
    const prior = previousById.get(filter.id);
    return "label_edited" in filter || !prior || !("label_edited" in prior) || prior.label !== filter.label
      ? filter : { ...filter, label_edited: prior.label_edited };
  });
}

function previousBases(draft: ReviewDraft): ReviewFilter[] {
  return Object.values(draft.filterBases).map((pair) => pair.base);
}

/** Adopt server-adjusted filters and bases, preserving their label provenance. */
export function applyReviewFilterResult(draft: ReviewDraft, result: { filters: ReviewFilter[]; base_filters?: ReviewFilter[] }): ReviewDraft {
  const filters = withLabelProvenance(result.filters, draft.filters);
  const bases = result.base_filters && withLabelProvenance(result.base_filters, previousBases(draft));
  return { ...draft, filters, filterBases: filterBases(filters, bases) };
}

/** Create an editable review snapshot with its saved baseline.
 * @param review Saved review to edit, or null for a new draft.
 * @param name Initial name for a new draft.
 * @param initialMcfDocument Catalog metadata to seed a new draft.
 */
export function createReviewDraft(
  review: SavedReview | null,
  name = "",
  initialMcfDocument?: ReviewMcfDocument,
): ReviewDraft {
  const emptyDocument: ReviewMcfDocument = {
    title: null, year: null, work_type: null, season: null, episode: null,
    source_url: null, imdb_url: null, release: null, comment: null,
    material_start_seconds: null, material_end_seconds: null,
  };
  const initialDocument = review?.mcf_document ?? initialMcfDocument ?? emptyDocument;
  const defaultStart = initialDocument.material_start_seconds ?? 0;
  const defaultEnd = initialDocument.material_end_seconds && initialDocument.material_end_seconds > defaultStart
    ? initialDocument.material_end_seconds
    : defaultStart + 1;
  const content: ReviewContent = {
    name: review?.name ?? "",
    memberships: review?.memberships ?? [],
    filters: review?.filters ?? [],
    filterBases: filterBases(review?.filters ?? [], review?.base_filters),
    mcfDocument: initialDocument,
    synchronization: review?.synchronization ?? {
      start_seconds: defaultStart,
      end_seconds: defaultEnd,
      end_mode: "before_credits",
    },
    acceptedCredits: review?.accepted_credits ?? null,
    mcfMetadataSeed: null,
    newManualFilters: [],
    communityMemberships: (review?.community_sources ?? []).map((source) => ({ source_id: source.source_id })),
  };
  return {
    ...clone(content),
    id: review?.id ?? stableId(),
    name: review?.name ?? name,
    revision: review?.revision ?? 0,
    policy_owner_id: review?.policy_owner_id ?? null,
    policy_revision: review?.policy_revision ?? 0,
    contentRevision: review?.content_revision ?? 0,
    edlContentRevision: review?.edl_content_revision ?? 0,
    mcfContentRevision: review?.mcf_content_revision ?? 0,
    baseline: contentState(content),
    baselineState: clone(content),
    removedSnapshots: {},
  };
}

export function isReviewDraftDirty(draft: ReviewDraft): boolean {
  return contentState(draft) !== draft.baseline;
}

/** Compare editable review content with its saved baseline, excluding its name.
 * @param draft Review draft to inspect.
 */
export function isReviewDraftContentDirty(draft: ReviewDraft): boolean {
  return contentState({ ...draft, name: "" }) !==
    contentState({ ...draft.baselineState, name: "" });
}

export function addReviewMembership(draft: ReviewDraft, membership: ReviewScanMembership): ReviewDraft {
  const existing = draft.memberships.find((item) => item.scan_id === membership.scan_id);
  if (existing?.role === membership.role) return draft;
  return {
    ...draft,
    memberships: existing
      ? draft.memberships.map((item) => item.scan_id === membership.scan_id ? { ...membership } : item)
      : [...draft.memberships, { ...membership }],
  };
}

type SnapshotRole = "audio" | "video";

function snapshotRoleForFilter(filter: ReviewFilter): SnapshotRole {
  return filter.source === "audio" ? "audio" : "video";
}

export function removedSnapshotKey(scanId: string, role: SnapshotRole): string {
  return `${scanId}:${role}`;
}

export function membershipHasRole(
  membership: ReviewScanMembership | undefined,
  role: SnapshotRole,
): boolean {
  return membership?.role === role || membership?.role === "both";
}

/**
 * Change one scan's membership while preserving exact role-local snapshots for the
 * current editing session. Newly requested roles with no cached snapshot are left
 * empty for the caller to populate from immutable source findings.
 */
export function changeReviewMembership(
  draft: ReviewDraft,
  membership: ReviewScanMembership | null,
  scanId = membership?.scan_id,
): ReviewDraft {
  if (!scanId) return draft;
  const existing = draft.memberships.find((item) => item.scan_id === scanId);
  let filters = draft.filters;
  let removedSnapshots = { ...draft.removedSnapshots };

  for (const role of ["audio", "video"] as const) {
    const hadRole = membershipHasRole(existing, role);
    const wantsRole = membershipHasRole(membership ?? undefined, role);
    const key = removedSnapshotKey(scanId, role);
    if (hadRole && !wantsRole) {
      const removed = filters.filter((filter) => (
        filter.source_scan_id === scanId && snapshotRoleForFilter(filter) === role
      ));
      filters = filters.filter((filter) => !(
        filter.source_scan_id === scanId && snapshotRoleForFilter(filter) === role
      ));
      if (removed.length) removedSnapshots[key] = clone(removed);
    } else if (!hadRole && wantsRole && removedSnapshots[key]) {
      filters = [...filters, ...clone(removedSnapshots[key])];
      delete removedSnapshots[key];
    }
  }

  return {
    ...draft,
    memberships: membership
      ? existing
        ? draft.memberships.map((item) => item.scan_id === scanId ? { ...membership } : item)
        : [...draft.memberships, { ...membership }]
      : draft.memberships.filter((item) => item.scan_id !== scanId),
    filters,
    removedSnapshots,
  };
}

export function removeReviewMembership(draft: ReviewDraft, scanId: string): ReviewDraft {
  return changeReviewMembership(draft, null, scanId);
}

export function restoreReviewMembership(draft: ReviewDraft, membership: ReviewScanMembership): ReviewDraft {
  return changeReviewMembership(draft, membership);
}

export function snapshotReviewFilters(draft: ReviewDraft, filters: ReviewFilter[]): ReviewDraft {
  return {
    ...draft,
    filters: [
      ...draft.filters,
      ...filters.map((filter) => ({ ...clone(filter), id: stableId() })),
    ],
  };
}

/** Changes to these fields mark a community filter as edited by the user (shared as their verdict). */
const COMMUNITY_EDIT_FIELDS = ["selected", "start_seconds", "end_seconds", "category", "subcategory", "severity", "edit_action"] as const;

/**
 * Apply a local filter edit. A direct change to a community filter's enabled state, times,
 * classification, severity or action marks it `community_edited`. A label change marks
 * `label_edited` immediately so the display follows the relabel; restoring the loaded label
 * restores its loaded flag (keeping the draft clean).
 */
export function updateReviewFilter(draft: ReviewDraft, filterId: string, patch: Partial<ReviewFilter>): ReviewDraft {
  const pair = draft.filterBases[filterId];
  const target = draft.filters.find((filter) => filter.id === filterId);
  if (target?.community_source_id && COMMUNITY_EDIT_FIELDS.some((field) => field in patch && patch[field] !== target[field])) {
    patch = { ...patch, community_edited: true };
  }
  if ("label" in patch && !("label_edited" in patch)) {
    const current = draft.filters.find((filter) => filter.id === filterId);
    const loaded = pair?.effective;
    if (current && patch.label !== current.label) {
      patch = { ...patch, label_edited: loaded && patch.label === loaded.label ? loaded.label_edited : true };
    }
  }
  return {
    ...draft,
    filters: draft.filters.map((filter) => filter.id === filterId ? { ...filter, ...patch, id: filter.id } : filter),
    filterBases: pair ? { ...draft.filterBases, [filterId]: {
      ...pair, base: { ...pair.base, ...patch, id: filterId },
    } } : draft.filterBases,
  };
}

export function deleteReviewFilter(draft: ReviewDraft, filterId: string): ReviewDraft {
  return { ...draft, filters: draft.filters.filter((filter) => filter.id !== filterId) };
}

export function stageManualReviewFilter(draft: ReviewDraft, filter: NewManualReviewFilter): ReviewDraft {
  return { ...draft, newManualFilters: [...draft.newManualFilters, { ...clone(filter), id: filter.id || stableId() }] };
}

export function applyReviewRetimeResult(draft: ReviewDraft, result: ReviewRetimeResult): ReviewDraft {
  const acceptedCredits = result.accepted_credits && draft.acceptedCredits
    ? { ...clone(draft.acceptedCredits), accepted_boundary_seconds: result.accepted_credits.accepted_boundary_seconds }
    : clone(result.accepted_credits);
  return {
    ...applyReviewFilterResult(draft, result),
    synchronization: clone(result.synchronization),
    policy_owner_id: result.policy_owner_id ?? draft.policy_owner_id,
    policy_revision: result.policy_revision ?? draft.policy_revision,
    mcfDocument: clone(result.mcf_document),
    acceptedCredits,
    newManualFilters: clone(result.new_manual_filters),
  };
}

/** Stage an accepted credits boundary in a review draft.
 * @param draft Current editable review.
 * @param result Accepted credits result from the API.
 */
export function applyCreditsAcceptance(draft: ReviewDraft, result: CreditsAcceptanceResult): ReviewDraft {
  const previousScanId = draft.acceptedCredits?.scan_id;
  const withoutPrevious = previousScanId ? {
    ...draft,
    memberships: draft.memberships.filter((item) => item.scan_id !== previousScanId),
    filters: draft.filters.filter((filter) => filter.source_scan_id !== previousScanId),
    removedSnapshots: Object.fromEntries(
      Object.entries(draft.removedSnapshots).filter(([key]) => !key.startsWith(`${previousScanId}:`)),
    ),
  } : draft;
  const withMembership = addReviewMembership(withoutPrevious, result.membership);
  const existingIndex = withMembership.filters.findIndex((filter) => (
    filter.source_scan_id === result.filter.source_scan_id
    && filter.source_finding_id === result.filter.source_finding_id
  ));
  const [acceptedFilter] = withLabelProvenance([result.filter], withMembership.filters);
  const acceptedBase = result.base_filter ? withLabelProvenance([result.base_filter], previousBases(withMembership)) : undefined;
  return {
    ...withMembership,
    filterBases: { ...withMembership.filterBases, ...filterBases([acceptedFilter], acceptedBase) },
    policy_owner_id: result.policy_owner_id ?? draft.policy_owner_id,
    policy_revision: result.policy_revision ?? draft.policy_revision,
    acceptedCredits: clone(result.accepted_credits),
    filters: existingIndex < 0
      ? [...withMembership.filters, acceptedFilter]
      : withMembership.filters.map((filter, index) => index === existingIndex ? acceptedFilter : filter),
  };
}

export function discardReviewDraft(draft: ReviewDraft): ReviewDraft {
  return {
    ...draft,
    ...clone(draft.baselineState),
    removedSnapshots: {},
  };
}

export function replaceReviewDraftAfterSave(draft: ReviewDraft, saved: SavedReview): ReviewDraft {
  void draft;
  return createReviewDraft(saved, saved.name);
}

export function forgetRemovedSnapshots(draft: ReviewDraft): ReviewDraft {
  return { ...draft, removedSnapshots: {} };
}

export function duplicateReviewDraft(draft: ReviewDraft, name: string): ReviewDraft {
  const duplicate = {
    ...createReviewDraft(null, name),
    memberships: clone(draft.memberships),
    // Community filters belong to the saved review's snapshots; a new review includes them again by link.
    filters: draft.filters.filter((filter) => !filter.community_source_id).map((filter) => ({ ...clone(filter), id: stableId() })),
    mcfDocument: clone(draft.mcfDocument),
    synchronization: clone(draft.synchronization),
    acceptedCredits: clone(draft.acceptedCredits),
    mcfMetadataSeed: null,
  };
  return duplicate;
}
