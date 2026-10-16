from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from pwdlib import PasswordHash

from app.core.config import settings


password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(subject: str) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": subject, "exp": expiry}, settings.jwt_secret, algorithm=ALGORITHM)


def token_subject(token: str) -> str | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        return None
