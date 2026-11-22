# Catalog scans and queue access

Users select a physical version and audio track, submit a scan, and manage their
own jobs without server paths or global queue controls.

## Sub-features

- `scan-selection` preserves the selected stream, version, models, and advanced options.
- `scan-submit` submits audio, video, and combined scans on accessible catalog items.
- `scan-actions` gates cancel, resume, rename, delete, and queue reorder independently.
- `scan-references` blocks deletion without disclosing hidden review names or IDs.
- `scan-permissions` discards protected results and pending selections after revocation.

## How to get to it (user POV)

- Open a downloaded Jellyfin item.
- Click **New Scan** in the selected version's detail view.
- Open **Open scans queue and history** to reach the **Scans queue and history** region.
- Every scan targets a Jellyfin item. The removed Other Files routes
  (`/api/manual-scans`, `/api/manual-files/*`) answer 404 even to the owner.

## Driving it with Playwright

Run the complete isolated access journey from the repository root.

```bash
node .github/skills/verify-editarr/scripts/scan-ui.mjs
```

The executable helper reuses launch, readiness, doctor, authentication, synthetic
media, masked capture, and cleanup from this skill. The owner and the member
sign in with Jellyfin, and the member is `scan-member`,
a Jellyfin user admitted by its first sign-in. The run Jellyfin stops at cleanup.
It uses ports 8980 through 8999 by default.
`VERIFY_PORT_MIN` and `VERIFY_PORT_MAX` select another explicitly assigned range.
No command-line flags are accepted. The evidence root is optional.

The helper generates real FFmpeg media and supplies a synthetic Jellyfin HTTP
transport. It enables the existing fake-model adapter through
a scratch-only Python import boundary. It does not replace browser API responses,
seed a current database, or bypass authentication or authorization.

First, once at 1440x900 (`lib/scan-foreign-jobs.mjs`, evidence
`scan-foreign-jobs.json`):

1. On the synthetic movie's page, the owner queues a video scan from **New Scan**
   through **Add to Queue** (201 from `POST /api/scans`). The interruption makes
   it `orphaned` with an error, which holds the queue, and a second owner scan
   waits `queued` behind it.
2. The member's queue API is `[]` and its history API, queue panel, badge, and
   page text contain neither job's ID, name, error, interruption path, or the
   server path. Cancel and resume by the foreign queue IDs answer 404.
3. The member queues its own scan behind them. Its
   `POST /api/scans/queue/reorder` gets 403 `capability_denied` and the stored
   `scan_queue` order is unchanged. The owner moves the member's job up from the
   queue panel (200), and both the stored order and the owner's queue API read
   it ahead of the waiting owner job (`reorder` in `scan-foreign-jobs.json`).
   The member's queue API lists only its own job, the badge reads 1, and the
   panel has one row and no reorder control. The member removes it.
4. The member's catalog page has no **Video file path** field.
5. The owner cancels the queued job and resumes the orphaned one to `complete`.
   The member's history must then show it, without its server path: completed
   findings are shared evidence for authorized media (`docs/multi-user-permissions.md`),
   and the member's default scope reaches the Jellyfin item.

Queue positions count only the jobs a caller sees. The owner's two jobs read
`sort_order` 0 and 1. The member's own job reads 0 in its create response and
its queue entry, although both owner jobs are ahead of it, while the owner then
reads 0, 1, 2 with the member's job last. `scan-foreign-jobs.json` records them
under `positions`.

At both 1440x900 and 320x900, the helper performs these checks.

1. Sign in as an ordinary user. Require safe catalog paths to be null, no path
   input, and no hidden settings, model-inventory, or path-mapping requests.
2. Open the exact **New Scan** dialog. Select `audio`, `video`, or `combined`
   through **Scan type**. Before changing a model, compare **Video model** and
   **Severity and Description model** with `/api/capabilities`: each option reads
   its display name plus " (recommended)" when `recommended` is true, only
   Qwen3-VL 8B is recommended, `d1-3b` is offered, and an untouched select shows
   the recommended entry (else the first). The simulated Linux host has no MLX,
   so NudeNet stays the video default there. The Severity and Description select
   must be described by its helper text. Then select audio stream `2`, `whisper`,
   `better_profanity`, `nudenet` (video) or `d1-3b` (combined), Severity and
   Description model Qwen3-VL 4B, six representative frames, and a two-second
   join gap where applicable. Fill the unique **Scan name (optional)** field.
3. Capture **Add to Queue** and require HTTP 201 with the exact name and options.
   Read protected queue and history APIs independently.
4. Wait for running fake-model progress before cancelling the audio job. Require
   persisted `cancelled`, not a disappeared spinner. Require no reorder buttons.
5. Complete the video job, rename it with Enter, delete it, and confirm both
   mutations through fresh history reads.
6. Interrupt the combined job with a permission error naming an unrelated model
   cache path. Observe `orphaned` in the queue. Require path-safe member HTTP and
   DOM diagnostics, unchanged owner detail, and the original exception in protected
   logs. Remove the interruption, resume through the UI, and require completion.
7. Create an owner-private review referencing that scan. Require the deletion
   blocker and disabled **Delete scan**. Require `has_hidden_references=true`
   and `reviews=[]`, with no private review name in the ordinary user's DOM.
8. Sign in as the owner in a fresh context and require other-user rename controls.
   Create a member review draft that includes the owner's shared scan. Delete
   that scan through the owner API, then save the member draft. Require 404
   `scan_not_found`, a fresh item GET 200, no persisted review, and the same
   workspace and draft. Cancel the existing navigation discard prompt and
   require the draft to remain (the prompt comes from the **Synthetic Movies**
   library link), then explicitly discard it before continuing.
9. Open **New Scan**.
   Deny the member `media.browse`, refresh on focus, and require **Page unavailable**
   with cleared dialog and results. Restore permission and await focus refresh.
10. Make the synthetic Jellyfin provider stop returning the open item, and have
   the owner save the Jellyfin connection again so Editarr drops its cached item.
   Require the page's next item read to return 404 `not_found`. Require
   the old heading, results, selection, and dialog to disappear while principal
   capabilities and permission revision remain unchanged. **Retry** must reread
   the item and refuse it while unavailable. When provider access returns, the
   owner runs `POST /api/jellyfin/sync`, because Editarr reuses a linked user's
   item decisions until that user's next sync; the page then reopens only after
   a fresh successful read. The old dialog must not reopen.

The helper checks viewport overflow, uncaught page errors, and WCAG axe results.
Right after setup, `lib/scan-connection-probes.mjs` writes
`scan-connection-routes.json`. The member gets 403 for the Jellyfin connection
test and disconnect. The owner's test answers 200 and reaches the synthetic
provider. The owner then disconnects Jellyfin, which reads back disconnected, and
reconnects it. Every removed Other Files route, and
`PUT /api/users/{id}/media-access/exceptions`, answers 404 to the owner. An EDL
upload whose target is `{"source": "manual", "video_path": ...}` gets 400
`jellyfin_item_required`, and the media item, scan, and review counts are
unchanged.

It retains `scan-ui-result.json`, `scan-ui-steps.json`, `scan-ui-http.json`,
`scan-ui-axe.json`, screenshots, and ARIA snapshots. Source-state JSON records the
last observed queue or history status. Steps include timestamps, and
`scan-worker-lifecycle.json` preserves protected application and worker logs before
scratch cleanup, including on failure. These synthetic logs pass through the same
secret-evidence scan as the other artifacts. `scan-private-diagnostics-*.json` records
the member/owner/log privacy comparison. `scan-media-revocation-*.json` records
the unchanged revision and fresh authorization read. Failure preserves `scan-ui-failure.json`,
the available browser states, and `scan-ui-state.json`: a read-only snapshot from
this run's scratch database with scan status, progress, error, PID, and queue ID,
but no worker tokens. A snapshot error is recorded without replacing the original
failure. Use it to distinguish an interrupted job from a lost queue row or stale
panel. Do not edit Python files anywhere under the checkout during measurement;
Uvicorn also reloads for ignored evidence files. Read `cleanup.json`, `redaction.json`, and
`doctor-after-cleanup.json` after every attempt. Processes must be stopped,
scratch removed, evidence retained, and redaction findings empty.

## Gotchas

- Review-target creation authorizes the destination first. Denied destinations
   must give identical responses for absent, hidden, unusable, and valid
   memberships, including destination revocation during capture. Only authorized
   destinations return identical `scan_not_found` status, body, and headers for
   absent, hidden, and unusable memberships, including source changes at the
   write fence. Run `tests/test_review_creation_missing_scan.py` with isolated
   data, logs, and an OS-temporary test directory. Default repository callers
   retain their existing behavior.
- `pnpm --dir web exec vitest run src/api/media-access.test.ts` checks that twenty
   concurrent operation failures share one pending item GET. Later failures and
   replacement workspaces must get a new read. No settled authorization is cached.
- Item-scoped operation errors do not prove media loss. Explicit media denials
   and coded identity/track read refusals clear protected content. Other coded
   403/404s require a fresh item read; unclassified responses never revoke it.
   A confirmed loss clears even a dirty review, with the explicit notice:
   "Your unsaved review draft could not be kept because this media is no longer
   available." Unrelated failures retain the draft and navigation discard guard.
- `scan-deleted-shared-draft-*.json` and the `scan-draft-*` captures record the
   deleted peer-scan race, retained draft, and canceled navigation prompt.
- This journey proves the real UI, API, authorization, and persisted job lifecycle.
  Synthetic transports do not certify live providers. Fake models do not certify
  inference, analyzed-audio provenance, acquisition, model quality, or downloaded weights.
- Real inference requires separately approved ready artifacts in the isolated
  model store. Never reuse private data or acquire weights without authorization.
- Requested audio selection is not actually analyzed audio. Fake-model results
  correctly leave analyzed provenance unknown.
- Orphaned jobs remain in the queue rather than completed history. Cancellation
  must occur after the fake adapter starts writing progress, not during startup.
- Permission restoration also needs a principal refresh before further navigation.
- Credits and controller-restart durability require their separate existing recipes.
- Artwork is absent from these synthetic providers. Provider artwork and backend
  outbound traffic beyond the recorded synthetic requests are not certified.
