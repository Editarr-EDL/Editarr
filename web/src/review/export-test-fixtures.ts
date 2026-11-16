import type { ExportPublication } from "../api/api-mcf-types";

/** A current personal EDL owned by user 1, overridable per scenario. */
export function testPublication(overrides: Partial<ExportPublication> = {}): ExportPublication {
  return {
    id: 41, publication_id: "00000000-0000-4000-8000-000000000041", publication_revision: 1,
    media_item_id: "media-1", media_source_id: "", format: "edl", scope: "personal", target: "self",
    target_user_id: 1, target_export_id: `local-${"1".repeat(32)}`, actor_id: 1, policy_owner_id: 1,
    original_author_id: 1, policy_revision: 1, review_id: "review-1", exported_format_revision: 1,
    content_sha256: "a".repeat(64), current: true, outdated: false, regeneration_state: "idle",
    regenerating: false, failed: false, failure_reason: null, retryable: false, serving: true,
    desired_policy_revision: null,
    ...overrides,
  };
}
