"""MU-23: every account's community credentials and pending proofs stay in its own home.

Accounts come from `live_accounts` (owner 1, admins 2 and 3, users 4 and 5) on a
migrated scratch database. Two accounts enroll, rotate, and recover at the same
time through `MockService`; no test contacts a community service.
"""

import asyncio
import json
import shutil

import pytest
from sqlalchemy import func, select

from scanner.db import CommunityConnectionRow, CommunityIdentityRow
from scanner.integrations.community.recovery import CommunityRecovery
from scanner.integrations.community.rotation import CommunityRotation
from scanner.integrations.community.transport import CommunityFailure
from scanner.persistence.migrations import adoption
from scanner.persistence.repository import repository_community
from scanner.persistence.repository.repository_community import (
    enqueue_operation,
    ensure_identity_ref,
    identity_connection,
    user_identity_home,
)
from scanner.security import community_identity as store
from scanner.security.community_key_recovery import recovery_source
from scanner.security.private_identity_files import DIRECTORY, IdentityHome
from tests.community_identity_migration_support import (
    CONTRIBUTOR,
    PERSONAL_FILTERS,
    build_community_database,
    identity_files,
)
from tests.community_identity_users_support import SERVICE, MockService, user_client
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER, live_accounts


@pytest.fixture
def accounts(tmp_path):
    live = live_accounts(tmp_path)
    yield live
    live.close()


def _identities(db) -> int:
    return db.scalar(select(func.count()).select_from(CommunityIdentityRow))


def _enroll(accounts, service, user_id, state_id):
    """Start and complete one account's enrollment; return its home."""
    with accounts.factory() as db:
        client = user_client(db, accounts.configuration.data_dir, user_id, service, create=True)
        service.authorize(client.start()["authorizationUrl"])
        asyncio.run(client.complete(state_id))
        return client.home


def test_an_account_has_no_identity_until_it_enrolls_and_then_exactly_one(accounts):
    data_dir = accounts.configuration.data_dir
    with accounts.factory() as db:
        owner = user_identity_home(db, data_dir, OWNER)
        assert user_identity_home(db, data_dir, USER) is None
        before = _identities(db)
        created = user_identity_home(db, data_dir, USER, create=True)
        assert created.credential_ref not in {owner.credential_ref, None}
        assert user_identity_home(db, data_dir, USER, create=True) == created
        assert _identities(db) == before + 1


def test_durable_activity_records_the_contributor_owner_and_actual_actor(accounts):
    service = MockService()
    home = _enroll(accounts, service, USER, "a" * 64)
    with accounts.factory() as db:
        connection = identity_connection(db, home.credential_ref, SERVICE)
        intent = dict(
            credential_ref=home.credential_ref,
            service_url=SERVICE,
            kind="vote",
            contributor_id=connection.contributor_id,
            installation_id=connection.installation_id,
            contribution_id="synthetic-contribution",
            revision_id="synthetic-revision",
            digest="a" * 64,
            vote="like",
        )
        own = enqueue_operation(db, operation_id="own-vote", **intent)
        assert (own.owner_id, own.actor_id) == (USER, USER)
        managed = enqueue_operation(db, operation_id="managed-vote", actor_id=ADMIN, **intent)
        assert (managed.owner_id, managed.actor_id) == (USER, ADMIN)
        db.commit()
    with accounts.factory() as db:
        retried = enqueue_operation(db, operation_id="managed-vote", actor_id=OWNER, **intent)
        assert (retried.owner_id, retried.actor_id) == (USER, ADMIN)


def test_a_concurrent_reservation_keeps_the_first_reference(accounts, monkeypatch):
    with accounts.factory() as db, accounts.factory() as racing:
        lookup = repository_community.identity_ref
        calls = []

        def stale_then_live(session, user_id):
            calls.append(user_id)
            if len(calls) == 1:
                ensure_identity_ref(racing, user_id)
                return None
            return lookup(session, user_id)

        monkeypatch.setattr(repository_community, "identity_ref", stale_then_live)
        reference = ensure_identity_ref(db, USER)
        monkeypatch.setattr(repository_community, "identity_ref", lookup)
        assert reference == lookup(racing, USER)
        rows = db.scalars(
            select(CommunityIdentityRow).where(CommunityIdentityRow.user_id == USER)
        ).all()
        assert len(rows) == 1


def test_incoming_files_move_only_into_the_owner_home(accounts, tmp_path):
    data_dir = accounts.configuration.data_dir
    staged = IdentityHome(tmp_path / "elsewhere", "a" * 32)
    restored = store.load_or_create_installation_identity(staged)
    (data_dir / DIRECTORY).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(staged.root, data_dir / DIRECTORY)
    with accounts.factory() as db:
        member = user_identity_home(db, data_dir, USER, create=True)
        owner = user_identity_home(db, data_dir, OWNER)
        assert not (data_dir / DIRECTORY).exists()
        adopted = store.load_or_create_installation_identity(owner)
        assert (adopted.installation_id, adopted.key_id) == (
            restored.installation_id,
            restored.key_id,
        )
        fresh = store.load_or_create_installation_identity(member)
        assert fresh.installation_id != restored.installation_id


def test_two_accounts_enroll_at_once_without_reading_each_others_secret(accounts):
    data_dir = accounts.configuration.data_dir
    service = MockService(overlap=2)
    sessions = [accounts.factory(), accounts.factory()]
    clients = [
        user_client(db, data_dir, user, service, create=True)
        for db, user in zip(sessions, (USER, PEER_USER), strict=True)
    ]
    installations = [service.authorize(client.start()["authorizationUrl"]) for client in clients]
    secrets = [
        store.load_or_create_installation_identity(client.home).client_state["pending"]["secret"]
        for client in clients
    ]

    async def both():
        return await asyncio.gather(clients[0].complete("a" * 64), clients[1].complete("b" * 64))

    results = asyncio.run(both())

    assert [result["connected"] for result in results] == [True, True]
    assert {installation for _, installation, _ in service.requests} == set(installations)
    for client, own, other in zip(clients, secrets, reversed(secrets), strict=True):
        manifest = (client.home.root / "identity.json").read_text()
        assert other not in manifest
        assert own not in json.dumps(client.status())
    contributors = set()
    for db, client, installation in zip(sessions, clients, installations, strict=True):
        row = identity_connection(db, client.home.credential_ref, SERVICE)
        assert row.installation_id == installation
        contributors.add(row.contributor_id)
        db.close()
    assert len(contributors) == 2


def test_two_accounts_rotate_at_once_and_each_keeps_only_its_candidate(accounts):
    data_dir = accounts.configuration.data_dir
    service = MockService()
    homes = [
        _enroll(accounts, service, user, state * 64)
        for user, state in ((USER, "a"), (PEER_USER, "b"))
    ]
    service.overlap = 2
    sessions = [accounts.factory(), accounts.factory()]
    clients = [
        user_client(db, data_dir, user, service)
        for db, user in zip(sessions, (USER, PEER_USER), strict=True)
    ]
    before = [store.load_or_create_installation_identity(home) for home in homes]

    async def both():
        return await asyncio.gather(
            *(CommunityRotation(client).run(prepare=True) for client in clients)
        )

    results = asyncio.run(both())

    after = [store.load_or_create_installation_identity(home) for home in homes]
    for old, new, result, db, home in zip(before, after, results, sessions, homes, strict=True):
        assert result["rotationCompleted"] and new.key_id != old.key_id
        assert new.installation_id == old.installation_id
        assert service.keys[new.installation_id] == new.public_key
        assert identity_connection(db, home.credential_ref, SERVICE).key_id == new.key_id
    candidates = [identity.rotation["newKeyId"] for identity in after]
    assert len(set(candidates)) == 2
    for db in sessions:
        db.close()


def test_an_interrupted_rotation_converges_while_another_account_rotates(accounts):
    data_dir = accounts.configuration.data_dir
    service = MockService()
    first, second = (
        _enroll(accounts, service, user, state * 64)
        for user, state in ((USER, "a"), (PEER_USER, "b"))
    )
    service.lose.add("/v1/keys/rotate")
    with accounts.factory() as db:
        with pytest.raises(CommunityFailure, match="network_unavailable"):
            asyncio.run(
                CommunityRotation(user_client(db, data_dir, USER, service)).run(prepare=True)
            )
        interrupted = store.load_or_create_installation_identity(first)
        assert interrupted.rotation["state"] == "pending"
        service.lose.clear()
        asyncio.run(
            CommunityRotation(user_client(db, data_dir, PEER_USER, service)).run(prepare=True)
        )
        identities = _identities(db)
        converged = asyncio.run(CommunityRotation(user_client(db, data_dir, USER, service)).run())
        again = asyncio.run(CommunityRotation(user_client(db, data_dir, USER, service)).run())

        assert converged["rotationCompleted"] == again["rotationCompleted"]
        active = store.load_or_create_installation_identity(first)
        assert active.key_id == interrupted.rotation["newKeyId"]
        assert service.keys[active.installation_id] == active.public_key
        other = store.load_or_create_installation_identity(second)
        assert other.rotation["state"] == "completed"
        assert identity_connection(db, first.credential_ref, SERVICE).key_id == active.key_id
        assert identity_connection(db, second.credential_ref, SERVICE).key_id == other.key_id
        assert _identities(db) == identities
        sent = [
            (path, body) for path, who, body in service.requests if who == active.installation_id
        ]
        assert len({body for path, body in sent if path == "/v1/keys/rotate"}) == 1
        assert sent[-1][0] == "/v1/keys/rotation-status"


def test_repeated_recovery_converges_without_touching_another_account(accounts):
    data_dir = accounts.configuration.data_dir
    service = MockService()
    first, second = (
        _enroll(accounts, service, user, state * 64)
        for user, state in ((USER, "a"), (PEER_USER, "b"))
    )
    untouched = identity_files(second.root)
    with accounts.factory() as db:
        identities = _identities(db)
        for attempt, state_id in enumerate(("c" * 64, "d" * 64)):
            old = store.load_or_create_installation_identity(first)
            (first.root / old.key_file).unlink()
            workflow = CommunityRecovery(user_client(db, data_dir, USER, service))
            workflow.start(old.key_id)
            service.expect_recovery(recovery_source(first), state_id)
            service.lose.add("/v1/keys/recover")
            with pytest.raises(CommunityFailure, match="network_unavailable"):
                asyncio.run(workflow.run(state_id=state_id, resend=True))
            service.lose.clear()
            done = asyncio.run(
                CommunityRecovery(user_client(db, data_dir, USER, service)).run(resend=True)
            )
            assert done["completed"] and done["pending"] is None, attempt
            active = store.load_or_create_installation_identity(first)
            assert active.contributor_id == old.contributor_id
            assert active.key_id == done["completed"]["newKeyId"] != old.key_id
            assert service.keys[active.installation_id] == active.public_key
            assert identity_connection(db, first.credential_ref, SERVICE).key_id == active.key_id
        assert _identities(db) == identities
        assert identity_files(second.root) == untouched


def test_the_migrated_owner_keeps_its_contributor_and_signs_with_its_keys(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    path = tmp_path / "data" / "scanner.db"
    path.parent.mkdir(mode=0o700, parents=True)
    files = build_community_database(path, tmp_path / "media", PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with sessionmaker(bind=engine)() as db:
            owner = db.execute(select(CommunityIdentityRow.user_id)).scalar_one()
            home = user_identity_home(db, path.parent, owner)
            assert identity_files(home.root) == files
            migrated = recovery_source(home)
            assert migrated.contributor_id == CONTRIBUTOR
            assert migrated.recovery["state"] == "pending"
            service = MockService()
            service.register(migrated)
            status = user_client(db, path.parent, owner, service).status()
            assert status["connected"] and status["identity"]["keyId"] == migrated.key_id
            assert status["pendingRecovery"] is True

            service.expect_recovery(migrated, "b" * 64)
            client = user_client(db, path.parent, owner, service)
            recovered = asyncio.run(CommunityRecovery(client).run(state_id="b" * 64, resend=True))
            assert recovered["completed"]["newKeyId"] == migrated.recovery["newKeyId"]
            asyncio.run(
                CommunityRotation(user_client(db, path.parent, owner, service)).run(prepare=True)
            )

            rotated = store.load_or_create_installation_identity(home)
            assert (rotated.contributor_id, rotated.installation_id) == (
                CONTRIBUTOR,
                migrated.installation_id,
            )
            assert service.keys[rotated.installation_id] == rotated.public_key
            assert not any(path.startswith("/v1/enrollments") for path, _, _ in service.requests)
            assert db.scalars(select(CommunityConnectionRow.contributor_id)).all() == [CONTRIBUTOR]
            assert _identities(db) == 1
    finally:
        engine.dispose()
