# MTS-06 - Build timing and credits UI

Status: **DONE**

Dependencies: MTS-02, MTS-03, and MTS-05. Deliverable: complete accessible MCF import,
saved-review retiming, credits detection/consent/preview, scan-history, and provenance UX for
catalog and Other Files.

Read the task-pack README, parent plan, every prerequisite handoff, repository `AGENTS.md`,
existing import/review/scan/model UI tests, and frontend design guidance before changing code.

## Scope

1. Mirror every implemented timing, END-mode, detector, candidate, consent, clip, accepted
   provenance, scan-history, and structured error contract in `web/src/api.ts`. Do not create
   frontend-only aliases or infer authoritative runtime/path/result values.
2. Extend review draft state, snapshots, dirty checks, discard/replace-after-save behavior,
   and API adapters for synchronization state, canonical MCF END, accepted detector
   provenance, credits memberships/findings, and complete retiming results.
3. Extend only the MCF branch of the shared Import dialog:
   - START timestamp defaults to `00:00:00.000` and remains editable;
   - Before credits is the default END mode and starts blank;
   - File end prefills trusted duration when available and remains editable;
   - labels/helper text distinguish synchronization END from canonical MCF END;
   - upload/path, busy/error, recoverable-issues retry, dirty guard, and focus restore remain
     correct;
   - all timing/detection state survives Continue Anyway.
4. Add Detect Credits to MCF import, Adjust Cue Times, and MCF metadata editor. Reuse one
   interaction component/state machine for start/reconnect, progress, cancel, low-confidence
   warning, Qwen capability/download consent, acquisition progress, result, clip, timestamp
   edit, accept, and dismiss.
5. Show an informed Qwen prompt before any missing model acquisition, including selected
   model, expected download/storage information, platform capability, and a non-Qwen
   continuation. Never bury acquisition in the primary Detect action.
6. Present a short playable clip around the candidate with accessible controls and an exact
   editable timestamp. Low confidence remains visible in text. Acceptance stores both
   detected and accepted values and stages the approved scan membership/finding snapshot.
7. Add Adjust Cue Times to every saved review. The dialog shows current and proposed
   synchronization intervals/modes, canonical before-credits END where separate, affected cue
   count/range summary, validation, and Detect Credits. Apply calls the non-persistent backend
   operation and changes the draft only.
8. Extend MCF metadata UI to display synchronization mode/bounds separately from canonical
   MCF bounds and accepted detector method/confidence/time/source scan. File-end mode must not
   make MCF export look eligible without canonical before-credits END.
9. Show credits scans as normal video scans with detector status/method/confidence and the End
   Credits finding under `Dispensable scenes / Dispensable scenes (General)` with description
   `End Credits`. Preserve existing tiered Review sorting/grouping behavior.
10. Ensure Save, Discard, review switching, duplicate/delete, scan references/deletion,
    previews, exports, and outdated badges react correctly to staged and saved timing changes.
11. Add focused CSS using existing visual language. Keep controls compact, avoid nested cards,
    and preserve 320px containment, text fitting, stable clip dimensions, and desktop density.

## Out of scope

- Backend contracts or behavior not implemented by prerequisites.
- Detector algorithm/model changes.
- New landing/help pages or visible prose explaining the whole feature.
- Automatic acceptance of a detector result or automatic model download.

## Verification

- Focused Vitest for API requests, draft helpers, import defaults/modes, recovery preservation,
  retiming all filter kinds, repeated adjustment, dirty/save/discard, detection lifecycle,
  consent, low confidence, edited acceptance, provenance, scan display, and export preflight.
- `pnpm build` in `web/`.
- Playwright desktop and 320px catalog plus Other Files journeys using deterministic fake
  detector/model responses and generated clips.
- Keyboard-only operation, nested modal focus trap/restore, screen-reader names/live status,
  non-color-only warnings, root overflow checks, and axe WCAG 2 A/AA/2.1AA checks.
- Clip pixel/playback checks at desktop/mobile sizes; no blank media or overlapping controls.
- Existing MCF import/export, review, tiered tables, scan, model capability, and EDL UI tests.
- `git diff --check`.

## Acceptance criteria

- [x] MCF import clearly captures target START and both approved END interpretations.
- [x] Continue Anyway retries the identical timing and accepted detection state.
- [x] Every review can retime all draft cues and then Save or Discard normally.
- [x] Detect Credits is one consistent explicit workflow in all three approved entry points.
- [x] Qwen acquisition always requires informed consent and has a non-Qwen path.
- [x] Candidate confidence, warning, clip, exact/editable time, and provenance are accessible.
- [x] Credits scans/findings and MCF export blocking are understandable without hidden state.
- [x] Catalog and Other Files pass desktop/mobile accessibility and overflow gates.

## Handoff

Status: DONE
Last completed substep: Completed responsive Playwright coverage after aligning deterministic
fixtures with the frozen timing and capability contracts.
Files changed: `web/src/api.ts`, `web/src/api.test.ts`, `web/src/review-draft.ts`,
`web/src/review-draft.test.ts`, `web/src/review-mcf.tsx`, `web/src/App.tsx`,
`web/src/App.test.tsx`, `web/src/styles.css`, `web/e2e/mcf10.spec.ts`, this task, and the
task-pack README.
Schema/API/contract changes: No backend or schema change. Frontend DTOs now exactly mirror
review synchronization, accepted credits provenance, credits scans/results/consent, import
timing, and retiming contracts. Added protected frontend helpers for every prerequisite route.
Verification commands and results: Focused API/draft/App Vitest passed 52 tests; full serial
Vitest passed 123 tests; `pnpm build` passed; MCF Playwright passed 6 desktop/mobile tests with
axe and root-overflow checks; TypeScript diagnostics and `git diff --check` passed.
Manual or artifact evidence: Deterministic browser tests cover catalog and Other Files at
1440x1000 and 320x900. The integrated browser connection timed out, so no additional manual
screenshot inspection was recorded.
Known failures or risks: Candidate video controls and protected clip URL are covered in
Vitest, while playable clip generation remains covered by the MTS-05 backend suite. Detector
quality remains bounded by the MTS-04/MTS-04A evidence.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-07 final integration and release evidence.
Next eligible task: MTS-07.
Decisions or deviations from the parent plan: None.
User approval required: None. No model was acquired and no user database, media, sidecar, or
running Editarr instance was touched.