"""API tokens for automation. Admin only; the raw token is returned exactly
once, from creation, and is never derivable from anything stored."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import apitokens
from app.security import Principal, require_admin

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("")
async def list_tokens(_: Principal = Depends(require_admin)):
    return {"tokens": apitokens.list_tokens()}


@router.post("")
async def create_token(request: Request, body: dict = Body(...),
                       principal: Principal = Depends(require_admin)):
    expires_days = body.get("expires_days")
    try:
        raw, rec = apitokens.create(
            name=str(body.get("name") or ""),
            role=str(body.get("role") or "viewer"),
            created_by=principal.name,
            creator_role=principal.role,
            expires_days=float(expires_days) if expires_days else None,
        )
    except (apitokens.TokenError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    request.state.audit_summary = f"created API token {rec['name']} ({rec['role']})"
    # The one and only time the raw token leaves the backend.
    return {"token": raw, "id": rec["sha256"][:12], "name": rec["name"],
            "role": rec["role"], "expires": rec["expires"]}


@router.delete("/{token_id}")
async def revoke_token(token_id: str, request: Request,
                       _: Principal = Depends(require_admin)):
    if not apitokens.revoke(token_id):
        raise HTTPException(404, "No such token")
    request.state.audit_summary = f"revoked API token {token_id}"
    return {"revoked": token_id}
