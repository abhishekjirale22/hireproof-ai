from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import token_subject
from app.database.session import get_db
from app.models.entities import Recruiter


bearer = HTTPBearer(auto_error=False)


def current_recruiter(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Recruiter:
    if credentials is None:
        raise HTTPException(401, "Recruiter authentication required.")
    subject = token_subject(credentials.credentials)
    if subject is None:
        raise HTTPException(401, "Invalid or expired access token.")
    recruiter = db.get(Recruiter, int(subject))
    if recruiter is None:
        raise HTTPException(401, "Recruiter account no longer exists.")
    return recruiter
