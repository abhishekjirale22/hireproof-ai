from threading import Lock

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import token_subject
from app.database.session import get_db
from app.models.entities import Recruiter


bearer = HTTPBearer(auto_error=False)
_PUBLIC_EMAIL = "public-judge@hireproof.local"
_public_reviewer_lock = Lock()


def _public_reviewer(db: Session) -> Recruiter:
    reviewer = db.scalar(select(Recruiter).where(Recruiter.email == _PUBLIC_EMAIL))
    if reviewer is not None:
        return reviewer
    with _public_reviewer_lock:
        reviewer = db.scalar(select(Recruiter).where(Recruiter.email == _PUBLIC_EMAIL))
        if reviewer is not None:
            return reviewer
        reviewer = Recruiter(name="Hackathon Judge", email=_PUBLIC_EMAIL, password_hash="public-access-disabled")
        db.add(reviewer)
        try:
            db.commit()
            db.refresh(reviewer)
        except IntegrityError:
            db.rollback()
            reviewer = db.scalar(select(Recruiter).where(Recruiter.email == _PUBLIC_EMAIL))
            if reviewer is None:
                raise
        return reviewer


def current_recruiter(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Recruiter:
    if credentials is None:
        if settings.public_access_mode:
            return _public_reviewer(db)
        raise HTTPException(401, "Recruiter authentication required.")
    subject = token_subject(credentials.credentials)
    if subject is None:
        raise HTTPException(401, "Invalid or expired access token.")
    recruiter = db.get(Recruiter, int(subject))
    if recruiter is None:
        raise HTTPException(401, "Recruiter account no longer exists.")
    return recruiter
