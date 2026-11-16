from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base


def review_caller(db):
    from scanner.persistence.repository.review_access import ReviewAccess
    from tests.scan_access_support import owner_caller

    return ReviewAccess(db, owner_caller(db).access)


def _database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)
