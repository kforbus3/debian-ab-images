"""User management. Admin only, including the listing -- who can log in and
with what rank is itself sensitive."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import sessions
from app import users as userstore
from app.security import Principal, require_admin

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(_: Principal = Depends(require_admin)):
    records = sorted(userstore.load().values(), key=lambda u: u.get("created") or 0)
    return {"users": [userstore.public(u) for u in records],
            "roles": list(userstore.ROLES)}


@router.post("")
async def create_user(request: Request, body: dict = Body(...),
                      _: Principal = Depends(require_admin)):
    try:
        rec = userstore.create(str(body.get("username") or ""),
                               str(body.get("password") or ""),
                               str(body.get("role") or "viewer"))
    except userstore.UserError as exc:
        raise HTTPException(400, str(exc))
    request.state.audit_summary = f"created user {rec['username']} ({rec['role']})"
    return userstore.public(rec)


@router.patch("/{username}")
async def update_user(username: str, request: Request, body: dict = Body(...),
                      _: Principal = Depends(require_admin)):
    """Change role, disabled flag, or password -- whichever fields are sent."""
    changes: list[str] = []
    try:
        if "role" in body:
            rec = userstore.set_role(username, str(body["role"]))
            changes.append(f"role={body['role']}")
        if "disabled" in body:
            rec = userstore.set_disabled(username, bool(body["disabled"]))
            changes.append("disabled" if body["disabled"] else "enabled")
            if body["disabled"]:
                # A disabled user's sessions must die with the flag, not
                # linger until they expire on their own.
                sessions.revoke_user(rec["username"])
        if "password" in body:
            rec = userstore.set_password(username, str(body["password"]))
            changes.append("password reset")
            # A reset password means the old sessions belong to whoever knew
            # the old password -- possibly the reason for the reset.
            sessions.revoke_user(rec["username"])
    except userstore.UserError as exc:
        raise HTTPException(400, str(exc))
    if not changes:
        raise HTTPException(400, "nothing to change: send role, disabled or password")
    request.state.audit_summary = f"updated user {rec['username']}: {', '.join(changes)}"
    return userstore.public(rec)


@router.delete("/{username}")
async def delete_user(username: str, request: Request,
                      _: Principal = Depends(require_admin)):
    try:
        userstore.delete(username)
    except userstore.UserError as exc:
        raise HTTPException(400, str(exc))
    sessions.revoke_user(username.strip().lower())
    request.state.audit_summary = f"deleted user {username}"
    return {"deleted": username}
