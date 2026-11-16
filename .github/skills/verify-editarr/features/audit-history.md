# Audit History

Feature ID `mu-audit-history`, owned by MU-28. Use the existing source verifier.
This recipe needs no provider, media, model, or pre-existing database.

## Sub-features

Admins can inspect history only for actions whose governing capabilities they
currently hold, within permitted accounts and media. Every required capability
must be held: combined credential changes need password and profile editing;
path create/update/reorder also need path viewing. Owner-default grants remain
delegable unless the capability registry reserves them to the owner. Enforced
instance-default policy rows additionally need nondelegable policy enforcement
and therefore stay owner-only. Unmapped or ambiguous historical actions stay
owner-only. New refusal writers preserve known allowlisted operation variants.
Live role defaults and per-user overrides apply.
The owner can
inspect full retained history, including key actions without key values.
Ordinary users have no audit navigation or data access. Filters and pagination
use server-scoped results. Refresh removes expired events and results that new
permissions deny.

## How to get to it (user POV)

Sign in as an admin or owner and choose **Audit history** in the sidebar.
Open `#/audit` for the list or `#/audit/{event_id}` for a detail deep link.
The page is separate from diagnostic downloads and operational settings.

## Driving it with Playwright

```sh
node .github/skills/verify-editarr/scripts/verify.mjs auth --audit
```

The existing helper checks prerequisites, creates its own temporary data and
log directories, launches the source app, and runs doctor. It creates the owner
through the real setup UI and checks fresh-context sign-in. The audit journey
then creates an admin and an ordinary user as Jellyfin users on the run's
Jellyfin and admits each by one Jellyfin sign-in (`admitJellyfinUser`); the
owner promotes the admin. It never seeds a database or exposes a test endpoint.

The owner issues and revokes two disposable keys through the production API.
The admin cannot see either owner-only issue event or the owner's permission
changes, even when account scope would permit the target. The revoke events
are the visible ordinary-target and hidden owner-target fixtures. The journey
checks scoped totals, pages, target filters, direct and guessed UUIDs,
and ordinary-user refusal. It drives `#/audit` and detail links at 1440x900 and
320x900, checks keyboard focus, axe, and page overflow, and captures masked
screenshots and redacted ARIA snapshots. It removes the admin's `audit.read`
through the real permission API and verifies that the already-open page loses
its navigation and data. Separately, denying `api_keys.revoke.other` removes
revocation counts and details, and allowing it restores them.

Unrelated audit writes do not invalidate prepared visibility evidence. A read
that cannot stabilize after three visibility changes returns private, no-store
503 `audit_history_busy` with `Retry-After: 1`. The view says "Audit history is
busy. Try again." Refresh retries the read. Authority loss still fails closed.

### Retention boundary

`scripts/audit_retention.py` calls the existing
`scanner.security.audit.store.run_retention(now=wall_clock + 91 days)` service
against only this run's disposable database. The helper refuses a mismatched
data directory, a non-verifier parent, a missing verification marker, or a
symlinked database. It does not migrate, seed, advance authentication time, or
touch an installed database. This is a controlled service-boundary proof, not a
91-day browser wait.

An owner and admin keep the same event open during deletion. Refresh must show
the generic unavailable state in both views; returning to the list must show
zero events. A same-browser switch to the ordinary account must show no previous
history, and neither browser store may hold an event ID or action.

### Evidence and cleanup

The printed evidence directory retains `audit-result.json`, `audit-steps.json`,
`audit-http-statuses.json`, `audit-browser-errors.json`, `audit-axe.json`,
`audit-retention.json`, and `audit-*.png` / `audit-*.aria.txt`. Read
`cleanup.json` and `redaction.json` after every attempt. All stop/remove/retain
flags must be true, cleanup errors empty, and secret findings empty. Doctor
must fail after cleanup because the recorded process group is not running.

Rehearse failure cleanup with the same command replacing `--audit` by
`--audit-inject-failure`. It deliberately exits 1 after the scoped admin UI
check, retaining failure evidence and running the same cleanup.

## Gotchas

Run `tests/test_audit_query.py` and `tests/test_audit_history_http.py` for retention
boundaries, corrupt-payload redaction, account protection, fresh-authority races,
action-capability scope, unrelated-write tolerance, busy retries, and MU-11 media
scope before filters/counts/details. The registry completeness check fails if a
new audit action lacks an explicit visibility rule. The HTTP fixture uses real
authentication and SQLite, with synthetic local media. It is not a live
Jellyfin or Arr certification. The browser recipe proves account scope, not
external provider behavior. Component tests live beside the audit view; shell
denial is covered by `web/src/test/App.access.test.tsx`.
