import type { ReactNode } from "react";
import type { useSavedReviewWorkspace } from "./review-workspace-state";

export type WorkflowStep = "scans" | "review" | "subtitles" | "publish";
export type GuidedReviewWorkspace = ReturnType<typeof useSavedReviewWorkspace>;

export type GuidedWorkflowProps = {
  scanStart: ReactNode;
  scanHistory: ReactNode;
  findFilters: ReactNode;
  onStartAudioScan: () => void;
  stageImportsForVerification?: boolean;
};
