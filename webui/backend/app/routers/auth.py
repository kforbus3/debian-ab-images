from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.config import settings
from app.security import create_token, require_auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    if form.password != settings.admin_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")
    return {"access_token": create_token(), "token_type": "bearer"}


@router.get("/check")
async def check(_: str = Depends(require_auth)):
    return {"ok": True}
