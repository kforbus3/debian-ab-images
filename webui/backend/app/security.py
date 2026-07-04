"""Single-admin JWT auth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=True)


def create_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.token_expire_hours)
    return jwt.encode({"sub": "admin", "scope": "session", "exp": expire},
                      settings.secret_key, algorithm=ALGORITHM)


def require_auth(token: str = Depends(oauth2_scheme)) -> str:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("scope") != "session":
            raise JWTError("wrong scope")
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return payload.get("sub", "admin")


# EventSource cannot send an Authorization header, so log streams use a
# short-lived single-purpose token in the query string instead of the session
# JWT (query strings end up in access logs; a 60-second scoped token is a much
# smaller thing to leak than a 12-hour session).
def create_stream_token(job_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=60)
    return jwt.encode({"sub": "admin", "scope": "stream", "job": job_id, "exp": expire},
                      settings.secret_key, algorithm=ALGORITHM)


def verify_stream_token(token: str, job_id: str) -> None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("scope") != "stream" or payload.get("job") != job_id:
            raise JWTError("wrong scope")
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired stream token")
