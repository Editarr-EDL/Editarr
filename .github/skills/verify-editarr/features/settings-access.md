# Verify settings access

The permission journey checks
owner, admin, user, and override-account settings at 1440 and 320 pixels,
independent API refusals, keyboard focus, axe, and a real controller restart.

## Run the isolated journey

Use the existing prerequisites from [Verify Editarr](../SKILL.md). The
`settings-access-v2` fixture creates three Jellyfin users on the run's Jellyfin
and admits each by one Jellyfin sign-in over the API (`admitJellyfinUser`),
promotes one to admin, and adds
synthetic path mappings. It never seeds a database or replaces API responses.
The override account has standalone mapping create/test and own-community view,
but cannot list mappings or enroll, rename, rotate, recover, or disconnect its
community identity. It retains the existing Connections-page admission grant.
The reserved `https://community.test` configuration gives the disconnected status
panel a meaningful state. The journey never initiates a remote community action.

Run from the repository checkout and choose an available port range.

```sh
node .github/skills/verify-editarr/scripts/verify.mjs auth --settings --controller --ports=9000-9019
```

The helper prints its evidence directory under `web/test-results/verify-editarr/`.
`--controller` runs the production API-only standalone controller with Vite.
It uses disposable data, logs, HOME, and native model cache, plus the existing
ownership doctor and cleanup. It does not launch the installed application.
`--ports` limits both loopback listeners to the given inclusive range.
Without that option, the existing random-port selection is unchanged.

The journey requires the default user's model catalog read (`GET
/api/capabilities` 200) and a selectable real model at both widths. No weights
are downloaded.

The helper verifies readiness and doctor before UI setup. To inspect a manual
instance, use `serve --controller --ports=9000-9019`
and `doctor EVIDENCE` from the existing helper. Stop only the owned helper.

## Inspect the proof

- Read `settings-write-probes.json` (`lib/settings-write-probes.mjs`). The
  operator, which may create but not edit mappings, gets 403 for the update,
  reorder, and delete of a probe mapping. The owner then updates it, reorders it
  first and back, and deletes it, and each step reads back. The admin's edit of
  another user's live severity gets 403. Once the owner grants
  `filters.preferences.edit.other`, the admin edits and restores it (200, read
  back), and after the grant returns to inherit the edit gets 403 again.
- Read `settings-steps.json`, `settings-http-statuses.json`, and
  `settings-axe.json`. Require all role, restart, archive, enforced-save, and
  deny-override steps. Disabled schedule saves must have independent API reads.
- The restart proof waits for the page's own reload after the applied generation
  (the navigation entry must be `reload`) and an enabled Restart control before
  capture and axe. Waiting only for the General heading races that reload.
- Require the admin deployment-view override at both widths. The real GET must
  succeed and PUT must remain forbidden. Active and next-start snapshots must be
  readable with no deployment form, inputs, or Save. The admin Connections page
  must retain its own Community scans panel without connection-edit permission.
- Require standalone mapping creation and resolution with no list GET or table.
  The owner independently reads the created mapping. Require the configured
  community status and Refresh control with no Connect action or mutation POST.
  Connections must not show the preceding General mapping status.
- Inspect `settings-filter-ready-1440.json` and `settings-filter-ready-320.json`.
  The owner opens the Server defaults tab (`#/settings/filters/server`). The
  Default rules heading, completed loads, the saved level in the Swearing chip's
  name, and the save bar's initially disabled Save establish readiness before
  editing. Choosing a new level in the chip's popover must enable Save.
  Development StrictMode replays mount effects. Abandoned filter loads must not
  publish ready state or overwrite a newer draft. Do not wait for a fixed count
  of requests or remove StrictMode to make this proof pass.
- Read `settings-catalog-1440.json` and `settings-catalog-320.json`. Record
  refused catalog reads as unavailable, not as successful acquisition forms.
- Open the masked PNG files and matching ARIA snapshots for each viewport.
  The read-only mapping table must have a named keyboard-focusable region.
- Require `settings-result.json` with `passed: true` before marking the entire
  journey passed. Require `settings-model-removal.json` proving scratch marker
  cleanup and idempotent deletion. A bundled acquisition must return null status
  for a user who cannot inspect global inventory.
- Require `cleanup.json` with stopped processes, removed scratch, retained
  evidence, and no errors. Require an empty `redaction.json.findings` list.
- Run `doctor EVIDENCE` after cleanup. It must refuse the stopped process group.
  Evidence must remain after that refusal.

`removed` reports whether the managed artifact directory existed. Clearing a
cancellation marker alone returns false. The journey compares that result with
scratch filesystem state, checks marker cleanup, then requires a repeated delete
to return false. No backend deletion contract changed.

The archive steps download a real full archive into scratch and require that
Settings offers no `Backup file`, `Account password`, or `Inspect backup`
control. They never activate restore on the source. They also check an enforced
filter edit with a wrong Jellyfin password, then a correct request-scoped
password and an independent revision read.
Both flows run at each viewport. Archive fixtures run before filter-default
mutations so these independent UI cases do not share changed defaults.
No model weights, real provider credentials, or real media are required.

## Required discovery dependencies

Standalone mapping create/test require their action grant and
`media.paths.view`, not `path_mappings.view`. Row update/reorder/delete retain
list access because their controls need the listed ID, content, and order.
Although the delete API accepts a known ID without path visibility, this UI
does not invent an ID-entry workflow.

Model cancel and delete controls require
`models.inventory.view` as well as the corresponding action grant. The controls
act on listed artifact IDs. The scan catalog's backend IDs are not artifact IDs,
and no existing no-inventory discovery API exposes them. The regression cases
require the controls hidden without inventory and present with inventory plus
action grants. Models-page admission remains inventory or download access.
Do not add artifact discovery or arbitrary-ID entry here.

Community status GETs require `community.identity.view.own`. Enrollment
start/complete/cancel require `community.identity.enroll.own`; profile
rename/discard require `community.identity.profile.own`; rotation and every
recovery POST require `community.identity.keys.own`. Keep readable status when
mutation grants are absent.
