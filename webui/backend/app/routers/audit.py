"""Reading the audit log. Admin only: the log names who did what from where,
which is exactly the reconnaissance a lesser credential should not get."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import audit as auditlog
from app.security import Principal, require_admin

router = APIRouter(tags=["audit"])


@router.get("/audit")
async def read_audit(since: float | None = None, limit: int = 200,
                     actor: str = "", _: Principal = Depends(require_admin)):
    """Newest first. `since` is a unix timestamp; `limit` caps the answer."""
    limit = max(1, min(int(limit), 2000))
    return {"events": auditlog.events(since=since, limit=limit, actor=actor)}
