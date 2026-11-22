import { useEffect, useRef, useState } from "react";
import { CommunityApiError } from "../api/api-community";
import { communityDownloadStatus, downloadCommunityFilter, type DownloadChoice, type DownloadOutcome } from "../api/api-community-downloads";
import type { Scan } from "../api/api-scan-types";
import type { LookupInput, RemoteFilterCandidate } from "../api/api-community-lookup";
import { resultChannels, type RemoteSelection } from "./community-result-state";
import { useCan } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";

type State = "queued" | "verifying" | "imported" | "reused" | "failed" | "cancelled" | "unknown";
export type DownloadEntry = { id: string; input: LookupInput; targetKey: string; choice: DownloadChoice; label: string;
  status: State; confirmed: boolean; error?: string; result?: { scanId: string; receiptState: string; reviewRequired: boolean; reviewed?: boolean } };
const entryId = (targetKey: string, choice: DownloadChoice) => JSON.stringify([targetKey, choice.contributionId, choice.revisionId, choice.artifactDigest, [...choice.channels].sort()]);
const choiceFor = (candidate: RemoteFilterCandidate, channels = resultChannels(candidate)): DownloadChoice => ({
  contributionId: candidate.contributionId, revisionId: candidate.revisionId, artifactDigest: candidate.artifactDigest, channels,
});
const savedResult = (outcome: DownloadOutcome) => ({ scanId: outcome.scanId!, receiptState: outcome.receiptState!, reviewRequired: outcome.reviewRequired!, reviewed: outcome.reviewed });

/** A sequential explicit queue; unknown outcomes are reconciled before any retry. */
export function useCommunityDownloads(input: LookupInput | null, onImported: () => void,
  onStaged?: (scan: Scan) => void) {
  const allowed = useCan("community.download");
  const lifetime = useCommunityLifetime();
  const [entries, setEntries] = useState<DownloadEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [checking, setChecking] = useState(false);
  const [statusError, setStatusError] = useState(false);
  const inputRef = useRef(input); inputRef.current = input;
  const notify = useRef(onImported); notify.current = onImported;
  const notifyStaged = useRef(onStaged); notifyStaged.current = onStaged;
  const entriesRef = useRef(entries); entriesRef.current = entries;
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const inputKey = JSON.stringify(input);
  const replace = (entry: DownloadEntry) => setEntries((previous) => [...previous.filter((value) => value.id !== entry.id), entry].slice(-96));
  const cancel = () => {
    generation.current++;
    pending.current?.abort();
    setRunning(false);
    setEntries((previous) => previous.map((entry) => entry.status === "verifying" ? { ...entry, status: "unknown" }
      : entry.status === "queued" ? { ...entry, status: "cancelled" } : entry));
  };
  useEffect(() => () => { cancel(); }, [inputKey]);

  const run = async (selected: DownloadEntry[], confirmed: boolean) => {
    const current = inputRef.current;
    if (!allowed || !lifetime.current() || !current || pending.current && !pending.current.signal.aborted || !selected.length || selected.length > 96
      || selected.some((entry) => JSON.stringify(entry.input) !== JSON.stringify(current) || entry.targetKey !== selected[0].targetKey)) return;
    const token = ++generation.current;
    const controller = new AbortController(); pending.current = controller;
    setRunning(true);
    selected.forEach((entry) => replace({ ...entry, status: "queued", confirmed, error: undefined }));
    try {
      const outcomes = await communityDownloadStatus(current.target, selected[0].targetKey, selected.map((entry) => entry.choice), controller.signal);
      for (const [index, entry] of selected.entries()) {
        if (!lifetime.current() || controller.signal.aborted || token !== generation.current) break;
        if (outcomes[index].status === "imported") {
          replace({ ...entry, status: "reused", result: savedResult(outcomes[index]), confirmed });
          notify.current();
          continue;
        }
        replace({ ...entry, status: "verifying", confirmed });
        try {
          const result = notifyStaged.current
            ? await downloadCommunityFilter(current, entry.targetKey, entry.choice, confirmed, controller.signal, true)
            : await downloadCommunityFilter(current, entry.targetKey, entry.choice, confirmed, controller.signal);
          if (!lifetime.current() || controller.signal.aborted || token !== generation.current) break;
          replace({ ...entry, status: result.status, confirmed, result: { scanId: result.scan.id, receiptState: result.receiptState, reviewRequired: result.reviewRequired, reviewed: result.reviewed } });
          if (result.verificationPending) notifyStaged.current?.(result.scan);
          notify.current();
        } catch (error) {
          if (!lifetime.current() || controller.signal.aborted || token !== generation.current) break;
          if (error instanceof CommunityApiError && [403, 404].includes(error.status)) {
            setEntries([]);
            break;
          }
          const uncertain = !(error instanceof CommunityApiError) || error.status === 0 || error.status >= 500;
          replace({ ...entry, status: uncertain ? "unknown" : "failed", confirmed, error: error instanceof CommunityApiError ? error.code : undefined });
        }
      }
    } catch (reason) {
      if (!lifetime.current() || controller.signal.aborted || token !== generation.current) return;
      if (reason instanceof CommunityApiError && [403, 404].includes(reason.status)) setEntries([]);
      else selected.forEach((entry) => replace({ ...entry, status: "unknown", confirmed }));
    } finally {
      if (lifetime.current() && token === generation.current) { setRunning(false); pending.current = null; }
    }
  };

  const reconcile = async (candidates: RemoteFilterCandidate[], targetKey: string) => {
    const current = inputRef.current;
    if (!allowed || !lifetime.current() || !current || running || !candidates.length) return;
    const key = JSON.stringify(current), token = generation.current;
    setChecking(true); setStatusError(false);
    try {
      const choices = candidates.map((candidate) => choiceFor(candidate));
      const outcomes = await communityDownloadStatus(current.target, targetKey, choices, lifetime.signal());
      if (!lifetime.current() || JSON.stringify(inputRef.current) !== key || token !== generation.current) return;
      for (const [index, outcome] of outcomes.entries()) {
        if (outcome.status !== "not_imported") {
          const choice = choiceFor(candidates[index], outcome.approvedChannels);
          replace({ id: entryId(targetKey, choice), input: current, targetKey, choice, label: candidates[index].summary.name || choice.revisionId,
            confirmed: false, status: "imported", result: savedResult(outcome) });
          notify.current();
        }
      }
      setEntries((previous) => previous.map((entry) => {
        const index = choices.findIndex((choice) => choice.revisionId === entry.choice.revisionId && choice.contributionId === entry.choice.contributionId
          && choice.artifactDigest === entry.choice.artifactDigest);
        const outcome = outcomes[index];
        if (entry.targetKey !== targetKey || entry.status !== "unknown" || !outcome) return entry;
        return outcome.status !== "not_imported" && entry.choice.channels.every((channel) => outcome.approvedChannels?.includes(channel))
          ? { ...entry, status: "imported", result: savedResult(outcome) }
          : { ...entry, status: "cancelled", error: undefined };
      }));
    } catch (reason) {
      if (lifetime.current() && token === generation.current) {
        if (reason instanceof CommunityApiError && [403, 404].includes(reason.status)) setEntries([]);
        setStatusError(true);
      }
    }
    finally { if (lifetime.current()) setChecking(false); }
  };
  return { allowed, entries: allowed ? entries.filter((entry) => JSON.stringify(entry.input.target) === JSON.stringify(input?.target)) : [], running, checking, statusError, cancel, reconcile,
    start: (candidates: RemoteFilterCandidate[], selections: RemoteSelection[], confirmed: boolean) => {
      if (!inputRef.current) return;
      const selected = selections.flatMap((selection) => {
        const candidate = candidates.find((value) => value.revisionId === selection.revisionId && value.artifactDigest === selection.artifactDigest);
        if (!candidate || selection.reviewRequired.length && !confirmed) return [];
        const choice = choiceFor(candidate, selection.channels);
        const id = entryId(selection.targetKey, choice);
        const existing = entriesRef.current.find((entry) => entry.id === id);
        return [{ ...existing, id, input: inputRef.current!, targetKey: selection.targetKey, choice,
          label: candidate.summary.name || candidate.revisionId, status: "queued" as const, confirmed }];
      });
      return run(selected, confirmed);
    }, retry: (entry: DownloadEntry, confirmed: boolean) => run([entry], confirmed || entry.confirmed) };
}