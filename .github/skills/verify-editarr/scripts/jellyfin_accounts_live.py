"""Live Jellyfin 12.0.0 proof of Jellyfin account admission and sync (MU-16).

Run from a checkout with `PYTHONPATH` set to it and scratch `EDITARR_DATA_DIR`
and `EDITARR_LOG_DIR`:

  jellyfin_accounts_live.py run [--inject-failure]
  jellyfin_accounts_live.py cleanup EVIDENCE_DIRECTORY

`VERIFY_JELLYFIN_PORT` publishes Jellyfin on that loopback port instead of a
random one.

`run` launches a disposable pinned Jellyfin container (the media-access
helper's launch, doctor, and cleanup), creates synthetic users, and drives
Editarr's real services against it on a scratch database: explicit import,
first sign-in creation, overlapping sign-ins, upstream disable and delete,
upstream re-enable, and an outage across the 24-hour grace on a fixed clock.
It always cleans up.
Evidence goes to web/test-results/verify-editarr/jellyfin-accounts-<run>/.
Exit codes: 0 pass, 1 failure, 2 blocked, 64 usage. Passwords and keys stay
in memory and the evidence is scanned for them.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_access_live as live  # noqa: E402
from media_access_live_docker import IMAGE, MARKER, cleanup, docker, doctor, save  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from scanner.db import (  # noqa: E402
    AuthSessionRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.integrations.jellyfin_setup import JellyfinSetupClient  # noqa: E402
from scanner.integrations.jellyfin_users import JellyfinUserAdapter  # noqa: E402
from scanner.persistence.migrations.runner import migrate_fresh_database  # noqa: E402
from scanner.security.access import link_state  # noqa: E402
from scanner.security.access.identity import CredentialKind  # noqa: E402
from scanner.security.access.load import load_access  # noqa: E402
from scanner.security.auth_repository_sessions import issue_session  # noqa: E402
from scanner.security.jellyfin_accounts import admission, reads  # noqa: E402
from scanner.security.jellyfin_accounts.owner_setup import set_up_owner  # noqa: E402
from scanner.security.jellyfin_accounts.sync import SyncRegistry, SyncTrigger  # noqa: E402
from scanner.settings.config import Settings  # noqa: E402

ROOT = live.ROOT


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


def check(results: dict, name: str, ok: bool, **observed) -> None:
    results[name] = {"ok": bool(ok), **observed}


def users(admin: live.Http) -> tuple[dict[str, str], dict[str, str]]:
    """Create the synthetic users; return their IDs and passwords."""

    ids, passwords = {}, {}
    for name, administrator in (("dana", True), ("erin", False), ("finn", False)):
        passwords[name] = secrets.token_urlsafe(18)
        ids[name] = admin.call(
            "POST", "/Users/New", json={"Name": name, "Password": passwords[name]}
        ).json()["Id"]
        if administrator:
            policy = admin.call("GET", f"/Users/{ids[name]}").json()["Policy"]
            admin.call(
                "POST", f"/Users/{ids[name]}/Policy", json={**policy, "IsAdministrator": True}
            )
    return ids, passwords


def editarr(
    scratch: Path, base: str, root_password: str, clock: Clock
) -> tuple[sessionmaker, Settings]:
    """A fresh scratch Editarr database whose owner is set up by Jellyfin's `root`.

    Setup links the owner to `root` and stores the key it mints, as the setup
    screen does.
    """

    path = scratch / "editarr" / "scanner.db"
    path.parent.mkdir()
    migrate_fresh_database(path, path.parent)
    factory = sessionmaker(bind=create_engine(f"sqlite:///{path}"))
    configuration = Settings(data_dir=path.parent)
    with factory() as db, JellyfinSetupClient(base) as client:
        set_up_owner(db, client, "root", root_password, clock=clock, issue=lambda user_id: user_id)
        db.commit()
    return factory, configuration


def drive(
    base: str,
    key: str,
    admin: live.Http,
    root_password: str,
    container: str,
    scratch: Path,
    results: dict,
) -> list[str]:
    """Run every account check against the live server; return the passwords used."""

    ids, passwords = users(admin)
    clock = Clock()
    factory, configuration = editarr(scratch, base, root_password, clock)
    link_state.link_clock = clock
    adapter = lambda: JellyfinUserAdapter(base, key, clock=clock)  # noqa: E731
    registry = SyncRegistry()

    def sync():
        with factory() as db:
            run = registry.run(db, SyncTrigger.MANUAL, clock=clock, adapters=lambda _: adapter())
        return {r.user_id: r for r in run.results}

    def linked(name):
        with factory() as db:
            return db.scalar(
                select(UserProviderIdentityRow.user_id).where(
                    UserProviderIdentityRow.provider_user_id == ids[name].replace("-", "").lower()
                )
            )

    def account(user_id):
        with factory() as db:
            row = db.get(UserRow, user_id)
            return row.role, row.status

    with factory() as db, adapter() as jellyfin:
        read = reads.begin_read(db, clock)
        listing = jellyfin.users()
        imported = admission.import_users(
            db,
            jellyfin,
            listing,
            frozenset({linked_id.replace("-", "").lower() for linked_id in (ids["dana"],)}),
            actor=1,
            credential=CredentialKind.SESSION,
            read=read,
        )
    dana = linked("dana")
    check(
        results,
        "import",
        [r.outcome for r in imported] == ["imported"] and account(dana) == ("admin", "active"),
        outcomes=[r.outcome.value for r in imported],
        account=account(dana),
    )

    def sign_in(name):
        with factory() as db, adapter() as jellyfin:
            return admission.sign_in(
                db,
                jellyfin,
                lambda: jellyfin.authenticate(name, passwords[name]),
                clock=clock,
                issue=lambda user_id: user_id,
            )

    erin = sign_in("erin")
    check(results, "firstLogin", account(erin) == ("user", "active"), account=account(erin))
    found, superseded = [], []

    def overlapping():
        try:
            found.append(sign_in("finn"))
        except admission.SignInRefused as refused:
            superseded.append(refused.refusal.value)

    threads = [threading.Thread(target=overlapping) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with factory() as db:
        identities = db.scalar(select(func.count()).select_from(UserProviderIdentityRow))
    check(
        results,
        "overlappingSignIns",
        len(set(found)) == 1
        and identities == len(["root", "dana", "erin", "finn"])
        and set(superseded) <= {"jellyfin_unavailable"},
        identities=identities,
        superseded=superseded,
    )

    with factory() as db:
        for user_id in (erin, linked("finn")):
            issue_session(db, remember_me=False, user_id=user_id, configuration=configuration)
    finn = linked("finn")
    policy = admin.call("GET", f"/Users/{ids['erin']}").json()["Policy"]
    admin.call("POST", f"/Users/{ids['erin']}/Policy", json={**policy, "IsDisabled": True})
    admin.call("DELETE", f"/Users/{ids['finn']}")
    clock.now += timedelta(minutes=5)
    outcome = sync()
    with factory() as db:
        open_sessions = db.scalar(
            select(func.count()).where(
                AuthSessionRow.user_id.in_([erin, finn]), AuthSessionRow.revoked_at.is_(None)
            )
        )
    check(
        results,
        "upstreamDisableDelete",
        outcome[erin].outcome.value == "disabled"
        and outcome[finn].outcome.value == "deleted"
        and account(erin)[1] == account(finn)[1] == "disabled"
        and outcome[dana].outcome.value == "verified"
        and open_sessions == 0,
        outcomes={str(k): v.outcome.value for k, v in outcome.items()},
        openSessions=open_sessions,
    )

    admin.call("POST", f"/Users/{ids['erin']}/Policy", json={**policy, "IsDisabled": False})
    clock.now += timedelta(minutes=5)
    outcome = sync()
    with factory() as db:
        causes = {user_id: db.get(UserRow, user_id).disable_cause for user_id in (erin, finn)}
        reopened = db.scalar(
            select(func.count()).where(
                AuthSessionRow.user_id == erin, AuthSessionRow.revoked_at.is_(None)
            )
        )
    signed_in = sign_in("erin")
    check(
        results,
        "upstreamReEnable",
        outcome[erin].outcome.value == "verified"
        and account(erin)[1] == "active"
        and causes == {erin: None, finn: "upstream_deleted"}
        and reopened == 0
        and signed_in == erin,
        outcome=outcome[erin].outcome.value,
        causes={str(k): v for k, v in causes.items()},
        reopenedSessions=reopened,
    )

    verified_at = clock.now
    docker("stop", "--time", "5", container)
    clock.now = verified_at + timedelta(hours=1)
    outage = sync()
    with factory() as db:
        within = load_access(
            db, dana, CredentialKind.API_KEY, now=verified_at + timedelta(hours=24)
        )
        after = load_access(
            db, dana, CredentialKind.API_KEY, now=verified_at + timedelta(hours=24, seconds=1)
        )
        stored = db.get(UserProviderIdentityRow, dana)
    check(
        results,
        "outageGrace",
        outage[dana].outcome.value == "unverified"
        and stored.link_state == "outage_grace"
        and stored.last_verified_at.replace(tzinfo=UTC) == verified_at
        and within.principal.account.link_state.value == "outage_grace"
        and after.principal.account.link_state.value == "outage_expired"
        and after.capabilities == link_state.PROFILE_ONLY & within.capabilities,
        failure=outage[dana].failure,
        withinCapabilities=len(within.capabilities),
        afterCapabilities=sorted(after.capabilities),
    )
    return list(passwords.values())


def run(inject_failure: bool) -> int:
    try:
        docker("version", "--format", "{{.Server.Version}}")
        docker("image", "inspect", IMAGE)
    except (OSError, subprocess.CalledProcessError) as error:
        raise live.Blocked(
            f"Docker and the pinned image are required ({IMAGE}): {error}"
        ) from error
    name = "jellyfin-accounts-" + secrets.token_hex(4)
    evidence = ROOT / name
    evidence.mkdir(parents=True)
    scratch = ROOT / f"{name}.scratch"
    scratch.mkdir()
    (scratch / MARKER).write_text(name)
    instance = {
        "run": name,
        "image": IMAGE,
        "container": name,
        "containerId": None,
        "volumes": [f"{name}-cache"],
        "volumeCreatedAt": {},
        "volumesBefore": docker("volume", "ls", "-q").stdout.split(),
        "port": os.environ.get("VERIFY_JELLYFIN_PORT", ""),
        "scratch": str(scratch),
        "checkout": str(live.CHECKOUT),
        "head": subprocess.run(
            ["git", "-C", str(live.CHECKOUT), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    save(evidence, "instance.json", instance)
    print(f"Evidence: {evidence}", flush=True)
    secrets_used: list[str] = []
    failures: list[str] = []
    results: dict = {}
    try:
        base = live.launch(instance, evidence)
        doctor(instance, base, evidence)
        if inject_failure:
            raise RuntimeError("injected failure after the doctor passed")
        admin, key, _, secrets_used = live.setup(base)
        secrets_used += drive(
            base, key, admin, secrets_used[0], instance["containerId"], scratch, results
        )
        failures = [k for k, v in results.items() if not v["ok"]]
        save(evidence, "results.json", results)
    except Exception as error:  # noqa: BLE001
        failures.append(f"{type(error).__name__}: {error}")
        save(evidence, "failure.json", {"error": failures[-1], "results": results})
    finally:
        left = cleanup(instance, evidence)
        clean_text = live.redaction(evidence, secrets_used)
    print(json.dumps({"failures": failures, "cleanup": left, "redacted": clean_text}, indent=1))
    return 1 if failures or not left["clean"] or not clean_text else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["run"] and set(argv[1:]) <= {"--inject-failure"}:
        try:
            return run("--inject-failure" in argv)
        except live.Blocked as blocked:
            print(f"BLOCKED: {blocked}", file=sys.stderr)
            return 2
    if argv[:1] == ["cleanup"] and len(argv) == 2:
        evidence = Path(argv[1]).resolve()
        left = cleanup(json.loads((evidence / "instance.json").read_text()), evidence)
        print(json.dumps(left, indent=1))
        return 0 if left["clean"] else 1
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
