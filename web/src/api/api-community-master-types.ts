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

/** `merged` and `unavailable` links keep their rows; the item page offers Find again. `replaced` is history. */
export type LinkState = "active" | "merged" | "unavailable" | "replaced";
export type MasterLinkSummary = {
  linkId: string; localVersionId: string; communityVersionId: string; label: string; aliasId: string | null;
  status: VersionStatus; unconfirmed: boolean; tier: MatchTier; matchPercent: number;
  appliedRevision: number; latestRevision: number; alignmentState: AlignmentState;
  alignment: Alignment | null; alignmentRevision: number | null;
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
export type MyVote = "up" | "down" | null;
/** A served master filter, read-only. `active`: it starts enabled when a review includes it. */
export type MasterFilter = {
  filterId: string; category: string; master: MasterValues; locallyVerified: boolean; myVote: MyVote; active: boolean;
};
/** The threshold and Show Low Confidence decide which filters start enabled in reviews.
 * `hideRejectedDownvoted` only hides downvoted rows from the master list. */
export type MasterPreferences = { confidenceThreshold: number; showLowConfidence: boolean; hideRejectedDownvoted?: boolean };
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

export type VoteValue = "up" | "down" | "none";
export type ReportReason = "spam" | "inaccurate" | "privacy" | "rights" | "other";
export type ReportRequest = { target: { type: "filter" | "version"; id: string }; reason: ReportReason; note?: string | null };
/** Vote and report envelope around the hosted service result. */
export type FeedbackResult = { version: 1; state: "completed"; result: unknown };

/** One filter of a link that starts enabled, on the local timeline (`GET …/active-filters`). */
export type ActiveMasterFilter = { linkId: string; filterId: string; category: string; confidence?: number | null } & FilterValues;
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
/** `changed`: a newer revision was downloaded and applied to the link (reviews refresh separately). */
export type CheckResult = { version: 1; changed: boolean; latestRevision: number };

export type VersionChoice = { choice: "alias"; alias_id: string } | { choice: "other"; text: string | null };
/** Master-set fields that the community-artifact-3 sharing preview adds to the manual sharing preview. */
export type MasterSharingSummary = {
  estimates: { approximate: boolean; newFilters: number; confirmations: number; adjustments: number; disputes: number };
  versionChoice: { choice: "alias"; aliasId: string; label: string } | { choice: "other"; text: string | null; label: string };
  work: { kind: string; tmdb: string | null; tvdb: string | null; title: string; year: number | null };
  entryCount: number; warnings: string[];
};
