"""The three OIDC routes: start a login, take the IdP's callback, hand the
session to the SPA. All open endpoints -- they exist to authenticate people
who by definition have no credential yet. The protocol work lives in
app/oidc.py; a successful login here ends exactly where a password login
ends: a users.json record and an opaque fls_ session.

Two kinds of failure get two kinds of answer. Protocol failures (forged
state, bad signature, expired token, unreachable IdP) are hard HTTP errors:
nobody legitimate sees them except through a stale tab, and a JSON error
beats pretending. Identity refusals (no mapped role, disabled here, username
collision with a local account) redirect back to the login page with the
reason in the URL fragment, because the person seeing them is a real user
whom the login page should tell what happened. Both paths write audit lines.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit, oidc, sessions, users

router = APIRouter(prefix="/auth/oidc", tags=["auth"])

_CB = oidc.CALLBACK_PATH


def _require_enabled() -> None:
    if not oidc.enabled():
        raise HTTPException(404, "Single sign-on is not configured "
                                 "(set OIDC_ISSUER and OIDC_CLIENT_ID)")


def _refused(request: Request, actor: str, why: str) -> RedirectResponse:
    """An identity-level refusal: audited as a 403, shown on the login page."""
    audit.record(actor=actor or "-", role="", method="GET", path=_CB,
                 status=403, ip=_ip(request), summary=f"SSO login refused: {why}")
    return RedirectResponse(f"/login#sso_error={quote(why)}", status_code=302)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/login")
async def login(request: Request):
    _require_enabled()
    try:
        url = oidc.begin(oidc.callback_uri(request))
    except oidc.OIDCError as exc:
        # The IdP being down must read as "SSO is down", never break the rest.
        raise HTTPException(502, str(exc))
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "",
                   error: str = "", error_description: str = ""):
    _require_enabled()
    if error:
        # The IdP itself said no (user cancelled, consent refused, ...).
        return _refused(request, "-", error_description or f"the identity provider answered {error}")
    handshake = oidc.pop_handshake(state)
    if not code or handshake is None:
        # A state we never issued, already used, or older than five minutes.
        # 400 rather than a friendly redirect: nothing about this request is
        # trustworthy enough to converse with.
        audit.record(actor="-", role="", method="GET", path=_CB, status=400,
                     ip=_ip(request), summary="SSO callback with unknown or expired state")
        raise HTTPException(400, "Unknown or expired sign-on attempt; please log in again")
    try:
        tokens = oidc.exchange_code(code, handshake["verifier"], oidc.callback_uri(request))
        claims = oidc.validate_id_token(tokens["id_token"], handshake["nonce"])
    except oidc.OIDCError as exc:
        audit.record(actor="-", role="", method="GET", path=_CB, status=401,
                     ip=_ip(request), summary=f"SSO token rejected: {exc}")
        raise HTTPException(401, str(exc))

    username = oidc.username_from(claims)
    role = oidc.resolve_role(claims)
    if role is None:
        return _refused(request, username or str(claims.get("sub", "-")),
                        "none of your groups map to a role here")
    try:
        rec = users.upsert_sso(username, role)
    except users.UserError as exc:
        return _refused(request, username, str(exc))

    token = sessions.create(rec["username"], ip=_ip(request))
    handoff = oidc.stash_session(token, rec["username"], rec["role"])
    audit.record(actor=rec["username"], role=rec["role"], method="GET", path=_CB,
                 status=200, ip=_ip(request), summary="logged in via SSO")
    # The code travels in the fragment: browsers never send fragments to
    # servers, so no access log on the way sees anything redeemable.
    return RedirectResponse(f"/login#sso={handoff}", status_code=302)


@router.post("/exchange")
async def exchange(request: Request, body: dict = Body(...)):
    """The SPA trades the one-time fragment code for the session token --
    the same shape the password login answers with, so the frontend stores
    it the same way. Login itself was audited by the callback; only a failed
    redemption is recorded here (that is someone replaying a code)."""
    _require_enabled()
    rec = oidc.redeem(str(body.get("code") or ""))
    if not rec:
        audit.record(actor="-", role="", method="POST", path="/api/auth/oidc/exchange",
                     status=401, ip=_ip(request),
                     summary="SSO handoff code invalid, expired or already used")
        raise HTTPException(401, "Sign-on expired or already used; please log in again")
    return {"access_token": rec["token"], "token_type": "bearer",
            "username": rec["username"], "role": rec["role"]}
