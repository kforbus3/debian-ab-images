"""Who is asking, and are they allowed: principals and role enforcement.

A principal is either a person's session (`fls_...`, see sessions.py) or an
automation token (`flt_...`, see apitokens.py); both arrive in the same
`Authorization: Bearer` header and resolve to a name and a role. Routers
declare the minimum role per endpoint with require_viewer / require_operator
/ require_admin -- explicitly, per endpoint, so what each role can reach is
readable in the router rather than inferred.

Two different refusals, deliberately distinct: no valid credential is 401
(the CI auth sweep and the nginx route tests depend on exactly that), while
a valid credential without the rank is 403 -- telling an operator "you are
not allowed" is correct, telling them "you are not logged in" sends them to
re-enter a password that will not help.

Sessions are checked against the user record on every request, so disabling
a user ends their access now, not at token expiry.

The design is deliberately pluggable for identity sources beyond passwords:
resolving a principal is one function, and a users.json record with no
password_hash and a `source` field is already a valid principal for it --
an SSO integration only needs to mint sessions for such users.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app import apitokens, sessions, users
from app.config import settings

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=True)


@dataclass
class Principal:
    name: str            # username, or token:<name> for an API token
    role: str            # viewer | operator | admin
    kind: str            # "session" | "token"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _resolve(token: str) -> Principal:
    """The principal behind a bearer credential, or 401."""
    if token.startswith(sessions.TOKEN_PREFIX):
        rec = sessions.resolve(token)
        if rec:
            user = users.get(rec["username"])
            # Checked live rather than trusted from the session: disabling or
            # deleting a user must end their sessions immediately.
            if user and not user.get("disabled"):
                return Principal(user["username"], user.get("role", "viewer"), "session")
    elif token.startswith(apitokens.TOKEN_PREFIX):
        rec = apitokens.resolve(token)
        if rec:
            return Principal(f"token:{rec['name']}", rec.get("role", "viewer"), "token")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                        headers={"WWW-Authenticate": "Bearer"})


def require_role(minimum: str):
    """Dependency: a principal of at least `minimum` rank, else 401/403."""
    async def dep(request: Request, token: str = Depends(oauth2_scheme)) -> Principal:
        principal = _resolve(token)
        # Stashed for the audit middleware, which runs after the endpoint and
        # would otherwise not know who the request belonged to.
        request.state.principal = principal
        if not users.role_at_least(principal.role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"the {principal.role} role cannot do this ({minimum} required)")
        return principal
    return dep


require_viewer = require_role("viewer")
require_operator = require_role("operator")
require_admin = require_role("admin")


# EventSource cannot send an Authorization header, so log streams use a
# short-lived single-purpose token in the query string instead of the session
# (query strings end up in access logs; a 60-second scoped token is a much
# smaller thing to leak than a 12-hour session). Still a JWT on purpose: it
# needs no revocation at 60 seconds, and SECRET_KEY keeps its HMAC job.
def create_stream_token(job_id: str, subject: str = "admin") -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=60)
    return jwt.encode({"sub": subject, "scope": "stream", "job": job_id, "exp": expire},
                      settings.secret_key, algorithm=ALGORITHM)


def verify_stream_token(token: str, job_id: str) -> None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("scope") != "stream" or payload.get("job") != job_id:
            raise JWTError("wrong scope")
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired stream token")
