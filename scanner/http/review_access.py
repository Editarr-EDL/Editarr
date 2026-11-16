"""FastAPI credential adapter and response projection for private reviews."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access


def run_review(caller, review_id, action, operation, *args, **kwargs):
    """Capture live catalog data before locking, after hiding unauthorized review IDs."""
    from scanner.repository import get_saved_review

    from .review_targets import capture_target

    caller.visible(review_id)
    review = get_saved_review(caller.db, review_id)
    prepared = capture_target(caller, review.target)
    return caller.run(review_id, action, operation, *args, prepared=prepared, **kwargs)


def review_caller(
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
) -> ReviewAccess:
    return ReviewAccess(db, access, request.state.access_credential.session_id)
