import type { Scan } from "../api/api-scan-types";
import type { Capability } from "../auth/capabilities";
import { can, type Principal } from "../auth/session";

const actions = {
  view: ["scan_jobs.view.own", "scan_jobs.view.other"],
  rename: ["scans.rename.own", "scans.rename.other"],
  delete: ["scans.delete.own", "scans.delete.other"],
  cancel: ["scan_jobs.cancel.own", "scan_jobs.cancel.other"],
  resume: ["scan_jobs.resume.own", "scan_jobs.resume.other"],
} as const satisfies Record<string, readonly [Capability, Capability]>;

export function scanActionAllowed(principal: Principal | null, scan: Scan, action: keyof typeof actions): boolean {
  if (!principal || typeof scan.is_own !== "boolean") return false;
  return can(principal, actions[action][scan.is_own ? 0 : 1]);
}