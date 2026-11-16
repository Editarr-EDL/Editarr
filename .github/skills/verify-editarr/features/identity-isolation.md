# Identity and Media Isolation

Feature `identity-isolation`. One run drives six accounts through the real
source application and a disposable Jellyfin server, then checks that no
account reaches another's media through counts, errors, artwork, browser
storage, deep links, API keys, or a change made while it is signed in.

## Actors

| Account | Kind | Media it may reach |
| --- | --- | --- |
| owner (`verification-admin`) | Jellyfin administrator, owner by setup | every library |
| `id-admin` | Jellyfin first sign-in, promoted to admin | Family by Jellyfin policy |
| `id-alice` | Jellyfin first sign-in | Movies by Jellyfin policy |
| `id-bob` | Jellyfin first sign-in | Family by Jellyfin policy, then nothing after a sync |
| `casey` | Jellyfin first sign-in | Family and Home by Jellyfin policy |
| `dana` | Jellyfin first sign-in | Movies by Jellyfin policy, then Movies and Family after a sync |

The journey creates `id-admin`, `id-alice`, and `id-bob` on the run's Jellyfin
with `addJellyfinUser`, sets their library policies there, and signs each in
through the UI.

## Run

Prerequisites are those of `auth`, plus FFmpeg (for the preview), Docker, and the digest-pinned image
`jellyfin/jellyfin@sha256:baba630419915985442f315f08b0cf46d9f4c8a0cc4bd38e94a6d35751dd5ef5`
(Jellyfin 12.0.0). The helper never pulls it. A missing prerequisite exits 2
with `blocked.json` before anything launches. `--identity` runs alone: combining
it with `--users`, `--settings`, `--audit`, `--community`, `--plugin-interop`, or
`--reviews` exits 64.

```bash
VERIFY_EVIDENCE_ROOT=web/test-results/identity-verification VERIFY_DOCKER_RUN_PREFIX=verify-identity- \
  node .github/skills/verify-editarr/scripts/verify.mjs auth --identity --ports=9200-9219
```

`--ports` bounds the three listeners: the API, Vite, and Jellyfin's host port.
`VERIFY_DOCKER_RUN_PREFIX` (default `identity-jellyfin-`) begins the
`editarr.verify.run` label on the container and volume, so a concurrent run
can identify its own resources. The same variable names the `auth --users`
provider's label (default `users-ui-jellyfin-`).

## External boundary

`scripts/users_ui_provider.py launch DIR PORT --libraries` reuses
media_access_live.py's launch, doctor, and cleanup. It writes the synthetic
Movies, Family, Shows, Home, and Songs libraries with FFmpeg from the same
image, completes the startup wizard, creates `casey` (Family and Home) and
`dana` (Movies), waits until every file is listed, and reports the host
directory behind the container's `/media`. The owner maps `/media` to it, so
Editarr's FFmpeg reads the same files. The journey uploads a 1x1 PNG as Film C's
primary image through Jellyfin's image API, because the synthetic items have no
artwork. The Editarr backend, UI, and Jellyfin client are real. Queues are not
configured in this journey; `scan-ui.mjs` covers them with its labelled
synthetic transport (see [scans](scans.md)).

## What it proves

HTTP, in each actor's own browser context:

- Each linked user's first `/api/libraries` read after sign-in is 200. There is
  no retry; any other status fails the run and is kept in `firstLibraryReads`.
- The owner lists exactly the fixture's five libraries and items: Family has
  Film C, Home has Clip, Movies has Films A, B, E, F, and G, Shows has Show X,
  and Songs lists nothing (the catalog does not list audio). Every actor lists
  exactly the table above, with those totals and item names. Ordinary and linked
  users get no `path`.
- A hidden library listing and a hidden Movies item are 404 `not_found`, with
  the same body as a nonexistent ID read by that actor. Every actor's
  nonexistent library and nonexistent item are 404 `not_found`, and every
  actor's nonexistent-library answer equals the owner's, so the owner's unknown
  ID reads exactly like another actor's hidden library.
- Film C's artwork is `200` for the owner with `Cache-Control: private,
  no-store` and `Vary: Cookie, Authorization`, and the identical URL is `404`
  for dana.
- alice cannot list users (403); the admin can, but cannot change overrides
  (owner only, 403 `capability_denied`). alice's API key, issued with fresh
  authentication, is refused the directory (403), answers a hidden Family item
  like a missing one, reads her own Movies item, and cannot reveal itself
  (403 `interactive_session_required`).
- Preview bytes in one browser context (`lib/identity-preview.mjs`). dana
  imports a one-line EDL (mute 0.2 to 0.8 s) on Film A and her page fetches
  its mute preview, the source audio padded to one second around the mute (0
  to 1 s of the 1 s file): 200, `audio/mpeg`, `no-store`, with its SHA-256
  recorded. The same page signs out, alice (who can open Film A) signs in, and
  the same URL is 404 `not_found` with no audio and a different hash, both
  normally and with `cache: "force-cache"`. dana signs back in and gets 200
  with the same hash. A Jellyfin policy change and sync then remove her
  Movies, and the same URL in the same page is refused the same way. Between
  those, alice (Movies by Jellyfin policy) imports a different EDL (mute 0.5 to
  0.9 s, a preview window from 0.2 s) onto Film A (201) and her page plays her
  own preview (200, audio, `no-store`), whose hash must differ from dana's.
  After dana signs back in, her page reads alice's preview URL as 404
  `not_found` with none of its bytes. Jellyfin 12 omits `LibraryId` from
  `/Items`, so alice's import exercises the placement of such items by path.
  The journey asserts the absence (`preview.alice.jellyfinLibraryId: null`)
  and fails with a message naming that path if a provider image starts
  returning it.
- Jellyfin removes bob's libraries and opens Family to dana. bob still sees
  Family and dana only Movies until the admin's `POST /api/jellyfin/sync`; then
  bob's existing session lists nothing at its next request and dana's sees
  both. An ordinary user cannot start a sync (403).

Browser, at 1440 and 320 pixels:

- At 1440, each of the six actors' own pages opens its first reachable library
  item (bob, with no media, his former Family item, which renders "Library
  unavailable") and reloads. The page must come back as the same account, with
  the same `editarr.user-scope`, the same stored
  `editarr.user.<id>.library-last-route.<library>` value, the same view, and
  no title from a library the actor cannot reach.

- casey signs in by keyboard only (Tab, type, Enter; there is no sign-in method
  choice), sees Film C in Family with no Movies title or
  navigation button, survives a reload, and passes axe. At 1440 she reaches
  Home by Tab and Enter from the sidebar.
- casey's deep link to a Movies item, alice's to Family, and bob's to his
  revoked Family each render "Library unavailable" and no hidden title. alice
  also gets "Page unavailable" for `#/settings/users` and no Users control.
- In one context the owner opens a Movies item (storing
  `editarr.user.<id>.library-last-route.<library>`) and loads Film C's artwork,
  signs out, and alice signs in. Storage is rebound to alice, the owner's
  route value is gone, and the same artwork URL from the same page is `404`.
  After a reload the page is still alice, with the same scope, none of the
  owner's values, and the artwork still `404` `not_found`.
- With the Family listing held, the library shows `0 shown` and a disabled
  Refresh Library button. With it aborted, an alert offers Retry. Neither
  shows another library's titles. The hold and abort happen in the browser's
  network layer; the server is not replaced.
- bob's revoked state is checked in a page opened after the sync. His
  earlier session is checked over HTTP. No page is watched while it changes.
- Every browser API response of 400 or more is recorded with its actor, status,
  code, body, and session (`lib/identity-browser-failures.mjs`). A session is
  live on a page from its first request carrying that cookie until the page
  issues a sign-out of it (`POST /api/auth/logout`) that is answered
  successfully. Playwright can report the next request before
  that answer, after the browser already dropped the cookie, so the issue
  time counts. A 401 `authentication_required` is expected only in two cases.
  The request carried no cookie while the page held no live session (session
  `none`, a signed-out shell probe). Or the page had issued a sign-out of that
  cookie before the 401 arrived, the request left before that sign-out was
  answered, and the answer ended the session (`signed-out`). A request still
  in flight when the journey signs out may legitimately be refused. The
  app aborts its polls on sign-out, so such a 401 can have no readable body
  (`bodyRead: false`); that is accepted only inside this window. A
  cookieless request while the page held a live session (`dropped`), one sent
  with the old cookie after the sign-out answered, and one racing a sign-out
  that was never answered may not. A sign-out that fails at the network layer
  is itself recorded, with status `null`, and fails the run.
  Every other failure needs a registered expectation for that actor, path, and
  status (deep-link refusals, the cross-account artwork and preview reads, and
  Film A's missing artwork). A 404 must carry the expectation's code, which is
  `not_found` for every refusal. Film A's missing artwork is not a refusal. The
  server answers it with the detail "No primary image found" and no `code`,
  so its expectation names that detail and a `null` code. The journey asserts
  that exact body for the owner (`missingArtwork`) before it registers them.
  Anything else, or any page error, fails the run.

## Evidence

Under `VERIFY_EVIDENCE_ROOT/editarr-verify-<id>/`:

- `identity-result.json` holds `firstLibraryReads`, the per-actor matrix
  (libraries, totals, hidden and missing statuses), artwork headers, refusal
  codes, `missingArtwork` (Film A's 404 body), `preview` (allowed and refused
  reads with hashes), in-session results,
  sync counts, `missingLibrary` (the owner's answer every actor matched), `browser` (expected and unexpected failures, page
  errors), and actor IDs. It is rewritten as the run progresses, so a failure
  keeps the completed rows.
- `identity-ui.json` holds each actor's reload (`reloads`: item, scope, and
  stored route key), the denied headings, keyboard path, storage keys before
  and after the switch and after its reload, artwork statuses, and the loading
  and error regions.
- `ui-identity-*.png` and `.aria.txt` captures (inputs masked), `ui-axe.json`,
  `ui-steps.json`, `ui-http-statuses.json`, and `ui-browser-errors.json`.
- `identity-jellyfin-cleanup.json` must report `clean: true`, and
  `identity-jellyfin-redaction.json` and `redaction.json` no findings. The
  provider's own records, including `jellyfin.log` saved before cleanup, stay
  in the sibling `-jellyfin` directory.

After cleanup, `verify.mjs doctor EVIDENCE` must fail with "Recorded process
group N is not running". If a run is killed before its cleanup,
`users_ui_provider.py cleanup EVIDENCE-jellyfin` removes only the containers
and volumes that the run recorded and labelled.

## Clock boundaries

The browser journey cannot wait 24 hours or five minutes. The HTTP boundary
tests in `tests/test_identity_clock_boundaries.py` use the account sync fixture
(synthetic `JellyfinBoundary`, `editarr-jellyfin-users-1`) and one fixed clock
for sessions, link expiry, and the scheduler:

- One second past 24 hours of outage, a linked member's key loses the directory
  (403 `capability_denied`) but keeps its profile, while the linked owner keeps
  the directory and an owner-only change, and an admin cannot demote the owner.
- `JellyfinSyncScheduler.tick` applies an upstream disable at 5:00 and not at
  4:59; the member's key then gets 403 `account_disabled`.
- The owner's key cannot reveal a key or transfer ownership (403
  `interactive_session_required`) even with the right password, and a session
  past its limit cannot present fresh authentication (401).

The exact 24-hour edge and the scheduler's later ticks are pinned in
`tests/test_jellyfin_accounts_http.py` and
`tests/test_jellyfin_account_ordering.py`.

## Gotchas

- Earlier runs saw `GET /api/libraries` answer 502 to a browser right after a
  sign-in. Its cause is not established. It now fails the run, with the body in
  `browser.unexpected` and Editarr's log in `server.log` in the run's evidence
  directory. `-jellyfin/jellyfin.log` only shows whether Jellyfin itself erred.
- Every item has an `image_url`; an item without artwork answers 404 "No
  primary image found" even for the owner. Use an item with uploaded artwork.
- Sidebar libraries are buttons, not links.
