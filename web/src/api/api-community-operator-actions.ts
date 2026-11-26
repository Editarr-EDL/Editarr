import { OperatorAccessError } from "../settings/community-operator/runtime";
import { aliasIdentifier, array, attestationIdentifier, boolean, fields, identifier, literal, nullable, number, reasonText,
  text } from "./api-community-operator-checks";
import { operatorRequest, operatorResourceName, type OperatorResource } from "./api-community-operator";

export type OperatorCommand = "quarantine" | "restore" | "suspend" | "unsuspend" | "exclude_vote" | "restore_vote" | "resolve_report" |
  "confirm_version" | "merge_version" | "approve_alias" | "reject_alias";
/** Resources each command accepts (§7.8). */
export const operatorCommandResources: Record<OperatorCommand, readonly OperatorResource[]> = {
  quarantine: ["filter", "attestation", "submission", "contributor", "version"],
  restore: ["filter", "attestation", "submission", "contributor", "version"],
  suspend: ["contributor"], unsuspend: ["contributor"], exclude_vote: ["vote"], restore_vote: ["vote"], resolve_report: ["report"],
  confirm_version: ["version"], merge_version: ["version"], approve_alias: ["alias_suggestion"], reject_alias: ["alias_suggestion"],
};
/** Command-specific fields: `intoVersionId` for a merge; `aliasId`, or `newAliasId` with `label`, for an approval. */
export interface OperatorActionExtras { intoVersionId?: string; aliasId?: string; newAliasId?: string; label?: string }
export interface OperatorIntent extends OperatorActionExtras { version: 1; actionId: string; command: OperatorCommand;
  resource: OperatorResource; targetId: string; contributorId?: string; expectedRevision: number; reason: string }
export interface OperatorActionResult { version: 1; actionId: string; resource: string; revision: number; outcome: "applied" }
export interface RecomputeIntent { version: 1; actionId: string; versionIds: string[] | "all"; after?: string; reason: string }
export type RecomputeResult = { version: 1; actionId: string; versions: { versionId: string; revision: number; changed: boolean }[] } |
  { version: 1; jobId: string; complete: boolean; nextAfter: string | null };

const uuid = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const label = (value: unknown) => typeof value === "string" && value.trim() === value && [...value].length >= 1 &&
  [...value].length <= 64 && !/[\p{Cc}\p{Cf}]/u.test(value);
function validExtras(intent: OperatorIntent) {
  const present = (["intoVersionId", "aliasId", "newAliasId", "label"] as const).filter(key => intent[key] !== undefined).sort().join(",");
  if (intent.command === "merge_version") return present === "intoVersionId" && identifier(intent.intoVersionId) && intent.intoVersionId !== intent.targetId;
  if (intent.command === "approve_alias") return present === "aliasId" ? aliasIdentifier(intent.aliasId) :
    present === "label,newAliasId" && aliasIdentifier(intent.newAliasId) && label(intent.label);
  return present === "";
}
export const operatorActionResource = (intent: OperatorIntent) => operatorResourceName(intent.resource, intent.targetId, intent.contributorId);

/** Validate and send one reasoned moderation action; invalid intents throw before any request. */
export function writeOperatorAction(intent: OperatorIntent, signal: AbortSignal) {
  const resources = operatorCommandResources[intent.command];
  if (intent.version !== 1 || !uuid(intent.actionId) || !resources?.includes(intent.resource) ||
    !(intent.resource === "attestation" ? attestationIdentifier : identifier)(intent.targetId) ||
    (intent.resource === "vote" ? !identifier(intent.contributorId) : intent.contributorId !== undefined) ||
    !number(intent.expectedRevision) || intent.expectedRevision === Number.MAX_SAFE_INTEGER ||
    !text(intent.reason) || !reasonText(intent.reason) || !validExtras(intent)) throw new OperatorAccessError(400);
  return operatorRequest<OperatorActionResult>("/admin/v1/moderation", fields({ version: literal(1), actionId: literal(intent.actionId),
    resource: literal(operatorActionResource(intent)), revision: literal(intent.expectedRevision + 1), outcome: literal("applied") }), signal, intent);
}

/** Recompute listed versions, or one bounded `all` batch continuing after `after` (§7.8). */
export function writeOperatorRecompute(intent: RecomputeIntent, signal: AbortSignal) {
  const ids = intent.versionIds;
  if (intent.version !== 1 || !uuid(intent.actionId) || !reasonText(intent.reason) ||
    (ids === "all" ? intent.after !== undefined && !identifier(intent.after) :
      !Array.isArray(ids) || !ids.length || ids.length > 100 || new Set(ids).size !== ids.length || !ids.every(identifier) || intent.after !== undefined))
    throw new OperatorAccessError(400);
  const check = ids === "all"
    ? fields({ version: literal(1), jobId: literal(intent.actionId), complete: boolean, nextAfter: nullable(identifier) })
    : fields({ version: literal(1), actionId: literal(intent.actionId),
      versions: array(fields({ versionId: identifier, revision: number, changed: boolean }), 100) });
  return operatorRequest<RecomputeResult>("/admin/v1/recompute", check, signal, intent);
}
