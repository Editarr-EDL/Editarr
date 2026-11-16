"""Identity homes for tests that create community identities beside a database."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.backup.archive import DATABASE
from scanner.db import CommunityIdentityRow
from scanner.persistence.repository.repository_community import identity_connection
from scanner.security.community_identity_homes import adopt_incoming_identity, owner_home
from scanner.security.private_identity_files import IdentityHome

# Used when no database names the home, as in tests of the private files alone.
FILE_ONLY_REFERENCE = "f" * 32


def only_identity_ref(db: Session) -> str:
    """The reference of the one community identity a single-account test database holds.

    A session that held no transaction is left without one.
    """
    clean = not db.in_transaction()
    reference = db.scalars(select(CommunityIdentityRow.credential_ref)).one()
    if clean:
        db.rollback()
    return reference


def only_connection(db: Session, service_url: str, **options):
    """That identity's connection to `service_url`, if any."""
    return identity_connection(db, only_identity_ref(db), service_url, **options)


def client_home(db: Session, data_dir: Path) -> IdentityHome:
    """The home of the one identity in `db`, after moving any incoming files into it."""
    home = IdentityHome(Path(data_dir), only_identity_ref(db))
    adopt_incoming_identity(home)
    return home


def identity_home(data_dir: Path, database: Path | None = None) -> IdentityHome:
    """The owner's home named by `database`, or by the one `*.db` or staged database in `data_dir`.

    Like the application's resolver, this first moves any upgraded or restored
    identity into that home. Without such a database, a fixed home stands in, so
    file-only tests keep one identity per data directory.
    """
    data_dir = Path(data_dir)
    if database is None:
        candidates = sorted([*data_dir.glob("*.db"), *data_dir.glob(DATABASE)])
        database = candidates[0] if len(candidates) == 1 else data_dir / "scanner.db"
    found = owner_home(data_dir, Path(database))
    if found is None:
        return IdentityHome(data_dir, FILE_ONLY_REFERENCE)
    adopt_incoming_identity(found)
    return found
