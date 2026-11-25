import type { ReactNode } from "react";
import type { useSavedReviewWorkspace } from "./review-workspace-state";

export type WorkflowStep = "community" | "scans" | "review" | "subtitles" | "publish";
export type GuidedReviewWorkspace = ReturnType<typeof useSavedReviewWorkspace>;

export type GuidedWorkflowProps = {
  scanStart: ReactNode;
  scanHistory: ReactNode;
  /** The Scans tab's Recover Scans action. */
  recoverScans: ReactNode;
  /** The Community tab's content: Find Filters and the account's community filter sets. */
  community: ReactNode;
  onStartAudioScan: () => void;
  stageImportsForVerification?: boolean;
};
