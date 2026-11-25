/** Wire types for the local community master filter set routes (docs/community-local-api.md). */

export type MasterTarget = { source: "jellyfin"; item_id: string; media_source_id: string | null };
export type FindLabel = { alias_id: string } | { text: string } | null;

export type VersionStatus = "confirmed" | "candidate";
export type MatchTier = "1" | "2" | "3" | "4" | "5" | (string & {});

export type FindResult = {
  communityVersionId: string; label: string; aliasId: string | null; status: VersionStatus;
  unconfirmed: boolean; tier: MatchTier; matchPercent: number; runtimeAgreement: string | null;
  filterCount: number; contributorCount: number; trust: number; revision: number;
  alignmentRequired: boolean; linkedLinkId: string | null;
};
export type FindResponse = {
  version: 1; findKey: string; localVersionId: string; keysUsed: string[];
  results: FindResult[]; error: { code: string; retryable: boolean } | null;
};

export type AliasChoice = { kind: "alias"; aliasId: string; label: string } | { kind: "other"; label: string };
export type AliasResponse = { version: 1; choices: AliasChoice[] };

export type AlignmentState = "not_required" | "required" | "approved";
export type Alignment = { offsetMs: number; scale: number; anchors: number };
export type UpdateCounts = { new: number; changed: number; removed: number; nowLowConfidence: number };

/** `merged` and `unavailable` links keep their rows; the item page offers Find again. `replaced` is history. */
export type LinkState = "active" | "merged" | "unavailable" | "replaced";
export type MasterLinkSummary = {
  linkId: string; localVersionId: string; communityVersionId: string; label: string; aliasId: string | null;
  status: VersionStatus; unconfirmed: boolean; tier: MatchTier; matchPercent: number;
  appliedRevision: number; latestRevision: number; alignmentState: AlignmentState;
  alignment: Alignment | null; alignmentRevision: number | null; pendingUpdate: UpdateCounts;
  linkState: LinkState; mergedInto: string | null;
};
export type MasterLink = MasterLinkSummary;

export type FilterValues = {
  startMs: number; endMs: number; subcategory: string | null; severity: string | null; action: string;
};
export type MasterValues = FilterValues & {
  spread: { startMs: number; endMs: number } | null; confidence: number; contributorCount: number;
  support: { attestations: number; upvotes: number };
  negative: { downvotes: number; disputes: number; coverageNegatives: number };
  lowConfidence: boolean; present: boolean;
};
export type FilterState = "pending" | "verified" | "adjusted" | "rejected" | "low_confidence";
export type MyVote = "up" | "down" | null;
export type MasterFilter = {
  filterId: string; category: string; master: MasterValues; local: FilterValues | null; effective: FilterValues;
  state: FilterState; touched: boolean; locallyVerified: boolean; myVote: MyVote; active: boolean;
};
export type MasterFilterResult = { version: 1 } & MasterFilter;
export type MasterPreferences = { confidenceThreshold: number; showLowConfidence: boolean };
export type MasterPreferencesResult = { version: 1 } & MasterPreferences;
export type MasterView = { version: 1; link: MasterLink; preferences: MasterPreferences; filters: MasterFilter[] };

export type AlignmentAnchorRequest = { filter_id: string; local_start_ms: number };
export type AlignmentPreviewRequest =
  | { anchors: AlignmentAnchorRequest[]; offset_ms: null; audio_track_index: number | null }
  | { anchors: null; offset_ms: number; audio_track_index: number | null };
export type AlignmentPreview = {
  version: 1; token: string; alignment: Alignment;
  rows: { filterId: string; alignedStartMs: number; alignedEndMs: number }[];
};

export type FilterDecision =
  | { decision: "verify" | "reject" | "reset"; expected_state: FilterState }
  | { decision: "adjust"; expected_state: FilterState; start_ms?: number; end_ms?: number;
      subcategory?: string; severity?: string; action?: string };

export type VoteValue = "up" | "down" | "none";
export type ReportReason = "spam" | "inaccurate" | "privacy" | "rights" | "other";
export type ReportRequest = { target: { type: "filter" | "version"; id: string }; reason: ReportReason; note?: string | null };
/** Vote and report envelope around the hosted service result. */
export type FeedbackResult = { version: 1; state: "completed"; result: unknown };

/** One in-effect filter of a link on the local timeline (`GET …/active-filters`). */
export type ActiveMasterFilter = { linkId: string; filterId: string; category: string } & FilterValues;
export type ActiveMasterFilters = { version: 1; linkId: string; localVersionId: string; filters: ActiveMasterFilter[]; edl: string };

export type SyncNotification = {
  notificationId: string; linkId: string; itemId: string; mediaSourceId: string | null; title: string;
  libraryId: string; kind: "changed" | "merged" | "unavailable"; fromRevision: number | null; toRevision: number | null;
  createdAt: number; read: boolean;
};
export type SyncStatus = {
  version: 1; ran: boolean; state: "idle" | "running" | "failed"; lastRunAt: number | null;
  nextDueAt: number | null; lastError: string | null; unreadCount: number; notifications: SyncNotification[];
};
export type CheckResult = { version: 1; changed: boolean; latestRevision: number; pendingUpdate: UpdateCounts };

/** A row's accepted master values: the value fields plus `lowConfidence` and presence. */
export type BaselineValues = FilterValues & { lowConfidence: boolean; present: boolean };
export type UpdateGroup = "new" | "changed" | "removed" | "nowLowConfidence";
export type UpdateDecision = "accept" | "keep_mine" | "dismiss";
export type UpdateRow = {
  rowId: string; filterId: string; category: string; touched: boolean; changedFields: string[];
  mine: FilterValues | null; oldMaster: BaselineValues | null; newMaster: MasterValues | null;
  mergedInto: string | null; allowed: UpdateDecision[]; canAccept: boolean;
};
export type MasterUpdate = {
  version: 1; fromRevision: number; toRevision: number; alignmentRequired: boolean; alignmentApproved: boolean;
  groups: Record<UpdateGroup, UpdateRow[]>;
};

export type VersionChoice = { choice: "alias"; alias_id: string } | { choice: "other"; text: string | null };
/** Master-set fields that the community-artifact-3 sharing preview adds to the manual sharing preview. */
export type MasterSharingSummary = {
  estimates: { approximate: boolean; newFilters: number; confirmations: number; adjustments: number; disputes: number };
  versionChoice: { choice: "alias"; aliasId: string; label: string } | { choice: "other"; text: string | null; label: string };
  work: { kind: string; tmdb: string | null; tvdb: string | null; title: string; year: number | null };
  entryCount: number; warnings: string[];
};
