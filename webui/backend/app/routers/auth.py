import secrets
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.config import settings
from app.security import create_token, require_auth

router = APIRouter(prefix="/auth", tags=["auth"])

# Sliding-window throttle on failed logins (single admin, so a global window
# is enough to stop online brute force without any state store).
_MAX_FAILURES = 5
_WINDOW_SECONDS = 60
_failures: deque[float] = deque(maxlen=_MAX_FAILURES)


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    now = time.monotonic()
    if len(_failures) == _MAX_FAILURES and now - _failures[0] < _WINDOW_SECONDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many failed logins; try again in a minute")
    if not secrets.compare_digest(form.password.encode(), settings.admin_password.encode()):
        _failures.append(now)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")
    return {"access_token": create_token(), "token_type": "bearer"}


@router.get("/check")
async def check(_: str = Depends(require_auth)):
    return {"ok": True}
