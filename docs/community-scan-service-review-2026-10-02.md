# Community implementation review — October 2, 2026

> Historical review: provider-runtime test results below describe the reviewed
> commit, not current tooling. All community tests/builds now run on Node/SQLite;
> deployment and recovery use the [Docker runtime guide](community-service-docker.md).
> Cloudflare Tunnel/Access remains ingress/operator authentication.

Reviewed commit: `ebdee24`. Scope was the 40 checked-off tasks in
`docs/community-scan-service-tasks/README.md`: CSS-00–23, CSS-24A,
CSS-26–35, CSS-35A/B/C, CSS-70 and CSS-71. CSS-25/36 are explicitly
in progress; publication, lookup, donation/import and later tasks were not
treated as missing completed functionality.

Seven actionable implementation findings follow. This was a review; application
code and the task completion ledger were not changed. Reproductions used temporary
databases, synthetic media and mocked hosted responses.

1. **[P1] Keeping the target community identity still replaces its database registration.**

   Location: `scanner/backup/activation.py:285–318`.
   Affected work: CSS-18/21 and the CSS-20/71 restore flows.

   `move_community_identity` controls private files, but not
   `community_connections`. Settings restore unconditionally copies this table
   from the incoming settings tables; full restore takes it from the incoming
   database and never includes it in the target tables to preserve. With two
   enrolled installations, leaving **Move community identity** unchecked therefore
   combines the target private identity with the source contributor/installation/key
   registration. Activation succeeds, but subsequent `load_local_identity()` fails
   with `Database identity does not match the signing key`. Both normal connection
   and recovery use this consistency check, so refreshing cannot repair the mismatch.

   Reproduced for both full and settings restores, using separate enrolled source
   and target identities and the default false identity-move choice. Existing
   activation tests retain private files but use unenrolled identities, missing the
   database mismatch.

   Fix direction: select the database registration and private identity as one
   generation according to the identity choice. Check their consistency before
   activation, and test both identity choices with enrolled source/target fixtures.

2. **[P1] Restoring pending lost-key recovery can insert the target's unrelated private key.**

   Location: `scanner/backup/activation.py:331–337`.
   Affected work: CSS-18/33 integration.

   CSS-33 explicitly permits a pending recovery backup with the old private key
   absent: its manifest contains the retained public identity and prepared candidate.
   During activation, however, an absent source file falls back to `before / name`,
   even when moving the source identity was selected. If the target already has
   `current.key`, the restored source manifest is paired with that unrelated key.
   `recovery_source()` then fails with `Community identity public key does not match`.
   The archive previews correctly and activation reports success, but the promised
   recovery workflow is unusable.

   Reproduced in both full and settings modes with a genuinely missing source key,
   a prepared recovery, and an existing target key. Existing CSS-33 backup tests
   validate the staged archive rather than activating it onto an existing identity.

   Fix direction: preserve intentional absence when replacing the identity file
   set. Do not fill missing members from a different generation. Validate the
   assembled post-restore identity before publishing it.

3. **[P1] Connections settings force bypass-authenticated users out of the entire app.**

   Locations: `web/src/settings/settings-view.tsx:197–199`,
   `web/src/settings/community/community-state.ts:94–102`;
   integration with `scanner/security/auth_policy.py:372–376` and
   `web/src/api/api-transport.ts`.
   Affected work: CSS-29/30; existing Jellyfin/Radarr/Sonarr settings.

   Community settings mount unconditionally and immediately fetch protected
   community status. The backend deliberately requires a local administrator
   session even under external authentication or local-address bypass. Its 401
   goes through the shared API transport, which treats it as global session expiry
   and unmounts the app. Merely opening Connections therefore replaces all source
   settings with **SIGN IN TO CONTINUE**, even when community is not configured.

   Reproduced in Chromium for both supported bypass modes using the 401 already
   asserted by `tests/test_community_api.py`. Screenshots confirm the whole
   Connections page disappears. A forms login can restore access, but bypass
   users should not have to sign in just to use their existing source settings.

   Fix direction: gate community requests on a local forms session and show an
   explicit community-only sign-in action, while preserving the rest of Connections.
   Keep administrator/CSRF enforcement on the backend. Add whole-app tests combining
   bypass authentication with the real community authorization response.

4. **[P2] Selected audio tracks with delayed starts produce misaligned findings.**

   Locations: `scanner/pipeline/audio.py:270–272` and
   `scanner/pipeline/runner_audio.py:75–92`.
   Affected work: CSS-35A/C; scan findings, review playback and exported mute timing.

   Extraction maps the exact selected stream into a WAV, but does not preserve
   its media-timeline offset. Analysis returns WAV-relative timestamps and the
   runner writes them unchanged. Storing `track.start_seconds` in provenance does
   not correct the findings. This is a gap in the newly supported arbitrary-track
   workflow; the old extraction path also lacked offset handling.

   Reproduced with a synthetic five-second MP4 containing a normal first track
   and a one-second secondary AAC track delayed by two seconds. The selected
   stream reports start `1.976009...` seconds; extraction produces a roughly
   one-second WAV. A fake model finding at WAV time `0.25` is persisted at `0.25`
   rather than approximately `2.226009` on the media timeline. The test uses real
   media probing/extraction and mocks inference only.

   Fix direction: explicitly map extraction time to the playback timeline, including
   container start, stream offsets and decoder priming. Either preserve that timeline
   in the extracted signal or translate findings consistently. Test delayed secondary
   tracks and nonzero container starts through saved findings and exports.

5. **[P2] Recovery candidates inherit the old key's revoked status and cannot be sent.**

   Location: `scanner/integrations/community/recovery.py:105–112`, interacting with
   `scanner/integrations/community/transport.py:129–134`.
   Affected work: CSS-29/33 and restored inactive identities.

   The recovery client replaces the identity's key ID/public/private bytes with
   the candidate but retains `last_known_key_state`. For a saved revoked, retired
   or compromised identity, transport refuses every request with `recovery_required`
   before contacting the service. This also blocks the status probe. Fresh Discord
   recovery is precisely the path that should be able to prove possession of a
   replacement without granting the old key ordinary authority.

   Reproduced with an enrolled identity whose saved state is revoked: preparation
   succeeds, but confirming recovery raises locally and the mock service receives
   no request. Existing recovery tests start from an identity marked active even
   when its old private file is deleted.

   Fix direction: explicitly authorize candidate-proof transport for the narrow
   recovery routes, without reactivating the old identity or relaxing ordinary
   signing. Cover all supported inactive saved states.

6. **[P2] Transport drops the recovery errors used by reconciliation and the UI.**

   Location: `scanner/integrations/community/transport.py:63–99,190–194`.
   Affected work: CSS-29/31/33 integration.

   `_REMOTE_CODES` omits `recovery_expired`, `invalid_recovery` and
   `recovery_conflict`, although the Worker returns them and local recovery/UI
   code handles them. Real HTTP responses are converted to `remote_error`.
   This loses the expired/conflicting-authorization explanation and prevents
   `CommunityRecovery.run()` from taking its designed committed-operation lookup
   fallback for `recovery_expired` or `invalid_recovery`. The rotation handler's
   `key_operation_conflict` is also absent from the allowlist.

   Reproduced by sending each recovery code through the actual `httpx.MockTransport`
   boundary: every expected code becomes `remote_error`.

   Fix direction: align the allowlist with hosted error contracts and add transport
   tests for recovery errors plus the complete HTTP-to-reconciliation path. Keep
   arbitrary remote payloads excluded from errors/logs.

7. **[P2] Cancelling backup jobs never frees the four-job allowance.**

   Location: `scanner/http/backup_api.py:356–368` and admission at `:172–178`.
   Affected work: CSS-20/71.

   Cancellation changes state and removes the archive but leaves the job in
   `_jobs`. Admission counts all retained jobs, including cancelled jobs, and
   only prunes entries after 30 minutes. Four cancelled attempts therefore block
   a fifth with `Too many backup jobs; cancel a finished job first`, although
   cancelling cannot resolve the condition. Choosing another archive and failed
   attempts can consume the same slots. Setup restore shares this registry.

   Reproduced by creating and cancelling four restore jobs and attempting a fifth;
   it returns HTTP 429 without any archive upload or active operation.

   Fix direction: retire completed cancellations from admission accounting and
   clean their private directories, with corresponding cleanup when an in-flight
   cancelled operation settles. Test repeated cancellation and failure/retry cycles.

Verification results:

| Check | Result |
| --- | --- |
| Complete Python suite, local sockets/subprocesses allowed | 1,732 passed; 9 failed |
| Frontend unit suite | 181 passed, 26 files |
| Hosted Worker suite in local Cloudflare test runtime | 356 passed, 46 files |
| Frontend TypeScript + Vite production build | Passed |
| Existing Chromium browser suite | 74 passed; 1 failed |
| Focused community/audio browser visual capture | 6 passed at 320px/1440px |
| Additional review reproductions | 10 Python cases and 2 browser cases demonstrate the findings above |

Eight Python failures are the existing scan-naming route fixtures submitting audio
scans without the now-required selection (`tests/test_scan_naming_core.py:301`).
The browser failure is the catalog version/queue flow in
`web/e2e/app-layout.spec.ts:109`: its shared mock does not implement the new audio
inventory endpoint, leaving Add to Queue disabled. These tests need updating to
continue protecting naming, version selection and queue creation; their failures
alone do not prove that the corresponding production paths are broken. The remaining
Python failure is the Qwen prompt phrase assertion already documented as pre-existing
in the CSS-09 handoff. Initial sandbox-only failures were separated out by rerunning
with local sockets/subprocesses enabled and a disposable runtime.

Browser tests cover keyboard/focus, modal confirmation, axe checks and document
overflow at mobile/desktop sizes. Visual inspection found one additional existing
layout issue: at 320px, the Jellyfin source card's badge/fields/action row extend
past the viewport and the Disconnect control is clipped. Its shared styles predate
the CSS work (`d4f1f73`), so it is not counted among the seven community findings.
The document-width overflow assertion misses clipped descendants because the
outer shell uses `overflow-x: clip`. Element bounds and clickability should be
included in mobile regression checks.

The restore reconciliation hold is an explicit documented limitation, not a newly
discovered regression: completed restores currently pause queue admission until
downstream reconciliation. Live Discord/Cloudflare deployment, provider constraints,
Windows/Linux native packages and non-Chromium browsers were not verified here.
No production database was migrated or reset, and no identity or service was deployed.
