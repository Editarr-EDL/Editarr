# Import Filters

Users import EDL or MCF as a browser upload to create reviewable snapshots for
the selected physical media version. The workflow imports uploaded files.

## Sub-features

- `import-edl`: import supported EDL actions and report rejected input accurately.
- `import-mcf`: preserve MCF metadata and map source cues to explicit target bounds.
- `import-source`: upload is the only transport; the removed path routes answer 404.
- `import-persistence`: reload the imported saved review and inspect its cues.

## How to get to it (user POV)

- `Import` on a Jellyfin item's page (`/#/library/LIBRARY/item/ITEM`), for the
  selected version. Every import belongs to a Jellyfin item; there is no
  path-only entry.
- Both EDL and MCF use the file upload in the `Import` dialog. There is no
  `Scanner path` control.

## Driving it with Playwright

The automated recipe is
`node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews`.
It uses the skill's launch, doctor, masked evidence, and cleanup, and serves the
synthetic media as one movie from the labelled synthetic Jellyfin transport. A
production EDL upload with target `{"source": "jellyfin", "item_id": ...,
"media_source_id": ...}` creates the first review, then the browser imports MCF
from the item's page and reopens it in a fresh mobile context. It does not prove
a live Jellyfin server. The review policy step proves uploads are the only
transport over HTTP: `POST /api/edl-imports/path` and `POST /api/mcf-imports/path`
answer 404, a member upload naming another source gets 400
`jellyfin_item_required` and a malformed Jellyfin target 422, both saving
nothing, and the owner's EDL and MCF uploads save owner reviews and scans that
are deleted again (`review-upload-imports.json`).

Preconditions: a `verify.mjs serve` instance with a UI-created administrator,
a real probeable video inside its scratch directory served by a Jellyfin
connection (the synthetic transport or a separately approved live fixture), and
valid synthetic EDL/MCF inputs. No such media is seeded by baseline. Use
FFmpeg-generated media, never existing user files.

- **Reach the item.** Open the item's page and wait for its title heading.
- **Open.** Click `page.getByRole('button', { name: 'Import', exact: true })`;
  scope subsequent controls to `page.getByRole('dialog', { name: 'Import' })`.
- **EDL upload.** Choose exact button `EDL`, use
  `dialog.getByLabel('EDL file').setInputFiles(data + '/verification.edl')`, fill
  `Import review name` with `Verification EDL`, click `Import EDL`, and expect the
  named saved review. Require no `Scanner path` button in the dialog.
- **MCF upload.** Reopen the dialog, choose exact button `MCF`, and use
  `dialog.getByLabel('MCF file').setInputFiles(data + '/verification.mcf')`.
  Inspect `Target START` and initially blank `Before-credits END`; choose `File end`
  and fill `File END` from the actual server-probed duration before importing.
- **Read back.** Reload the page, reselect the same scratch media/version, and open
  `Open review Verification EDL`. Inspect saved cue times and source metadata;
  capture the POST result/status and a protected review GET, not just a toast.
- **Alternate versions.** Repeat both formats on each affected exact version.
- **Other-source target.** An upload whose target names another source (for
  example `{"source": "manual", "video_path": ...}`) must get 400
  `jellyfin_item_required` and save nothing; `scan-ui.mjs` and `upgrade` check it.
  A Jellyfin target with an empty or missing `item_id` is a 422 validation error.

## Gotchas

- Baseline has no video or sidecar. Without them this recipe is BLOCKED, not passed.
- The automated recipe creates its first review through the production upload
  API, never through a database seed or a test-only endpoint.
- Probe duration is a real prerequisite; do not invent timing to enable a button.
- A dirty saved-review draft disables import; save or cancel it explicitly first.
- MCF schema errors are not interchangeable with explicitly ignored recoverable
  errors. Keep invalid-input proof separate from a successful import.
- Existing selector examples in the [browser tests](../../../../web/e2e/), including
  `web/e2e/app-review.spec.ts` use mocked APIs; borrow selectors, not mock results.
