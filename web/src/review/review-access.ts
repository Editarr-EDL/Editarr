import { useEffect, useRef, useState } from "react";
import { useOptionalAuthenticationGate, usePrincipal } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { can } from "../auth/session";
import type { SavedReview } from "../api/api-review-types";
import { readAccount } from "../api/api-users";

export function useReviewAccountLabel(review: SavedReview | null) {
  return useAccountLabels([review?.owner_id, review?.original_author_id, review?.policy_owner_id]);
}

/** Label account IDs as You, their directory name when `accounts.list` allows it, or Another account. */
export function useAccountLabels(ids: readonly (number | null | undefined)[]) {
  const principal = usePrincipal();
  const userId = principal?.userId;
  const directoryAllowed = principal !== null && can(principal, "accounts.list");
  const key = [...new Set(ids.filter((id): id is number => id != null))].sort((a, b) => a - b).join(",");
  const [accounts, setAccounts] = useState<{ principalId: number; labels: Record<number, string> } | null>(null);
  useEffect(() => {
    if (!directoryAllowed || userId == null || !key) return;
    const controller = new AbortController();
    for (const id of key.split(",").map(Number)) {
      if (id === userId) continue;
      void readAccount(id, controller.signal).then(account => {
        if (controller.signal.aborted) return;
        const label = account.display_name || account.username;
        if (label) setAccounts(current => ({ principalId: userId,
          labels: { ...(current?.principalId === userId ? current.labels : {}), [id]: label } }));
      }).catch(() => {});
    }
    return () => controller.abort();
  }, [directoryAllowed, userId, key]);
  return (id: number | null | undefined) => id == null ? "Unavailable"
    : id === principal?.userId ? "You"
      : (directoryAllowed && accounts?.principalId === principal?.userId && accounts?.labels[id]) || "Another account";
}

export function useReviewLifetime() {
  const gate = useOptionalAuthenticationGate();
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  return () => active.current && (gate?.isAuthenticationCurrent() ?? true);
}

export function useReviewAccess(review: SavedReview | null) {
  const principal = usePrincipal();
  const gate = useOptionalAuthenticationGate();
  const other = review?.owner_id != null && review.owner_id !== principal?.userId;
  const allowed = (capability: Capability) => gate === null || (principal !== null && can(principal, capability));
  const field = (name: "evidence" | "filters" | "cues" | "metadata" | "timing" | "credits") => allowed(`reviews.${name}.edit.${other ? "other" : "own"}`);
  return { field, allowed, other,
    create: allowed("reviews.create.own"),
    save: allowed(other ? "reviews.edit.other" : "reviews.save.own"),
    rename: allowed(other ? "reviews.edit.other" : "reviews.rename.own"),
    duplicate: allowed(other ? "reviews.copy.other" : "reviews.duplicate.own"),
    remove: allowed(other ? "reviews.delete.other" : "reviews.delete.own"),
    preview: allowed(other ? "reviews.preview.other" : "reviews.preview.own"),
  };
}
