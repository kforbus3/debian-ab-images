"""OpenID Connect single sign-on: the protocol half, no HTTP endpoints.

Authorization-code flow with PKCE against any conforming IdP (Keycloak,
Authentik, Entra, Okta). This module owns everything that is not a route:
provider discovery, JWKS handling, the short-lived handshake state, ID-token
validation, and turning IdP groups into local roles. The routes themselves
live in routers/oidc.py; the user records it produces are ordinary users.json
records with `source: "oidc"` and no password hash (see users.py -- such a
record is already a valid principal by design).

The feature is entirely off unless OIDC_ISSUER and OIDC_CLIENT_ID are both
set, and every network call here happens inside the /api/auth/oidc/* requests
only -- a down or misconfigured IdP makes those requests fail with a clear
message and affects nothing else. Password login never waits on an IdP.

Discovery and JWKS are fetched lazily and cached for an hour; if a refresh
fails, the stale copy is kept -- an IdP restarting must not log the fleet's
operators out of the login page. Handshake state (state, nonce, PKCE
verifier) lives in memory with a five-minute expiry: it does not survive a
backend restart, and does not need to -- a login abandoned across a redeploy
is retried in one click -- but concurrent logins each get their own entry
keyed by the state value, so they cannot collide.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app import users
from app.config import settings

CALLBACK_PATH = "/api/auth/oidc/callback"

DISCOVERY_TTL = 3600          # how long a fetched discovery doc / JWKS is trusted
HANDSHAKE_TTL = 300           # authorize redirect -> callback, generous
HANDOFF_TTL = 60              # callback redirect -> SPA exchange, one page load
_MAX_PENDING = 1000           # bound on abandoned handshakes/handoffs held in memory

# Only asymmetric algorithms: HS256 would let anyone who knows the (public)
# client_id forge ID tokens if it were ever accepted here.
_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

_lock = threading.Lock()
_discovery: dict[str, tuple[float, dict]] = {}   # issuer -> (fetched_at, doc)
_jwks: dict[str, tuple[float, dict]] = {}        # jwks_uri -> (fetched_at, keys)
_handshakes: dict[str, dict] = {}                # state -> nonce/verifier/created
_handoffs: dict[str, dict] = {}                  # one-time code -> session record


class OIDCError(Exception):
    """A protocol-level failure. The message is safe to show and to audit."""


def enabled() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id)


def callback_uri(request) -> str:
    """The redirect_uri, derived from the request's own origin.

    The documented deployment is behind a TLS reverse proxy, so the proxy's
    X-Forwarded-Proto/Host win over what uvicorn saw on the socket --
    otherwise the IdP would be asked to redirect to http://localhost:8080.
    """
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}{CALLBACK_PATH}"


def _fetch_json(url: str, what: str) -> dict:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 - network/JSON/HTTP alike: unreachable IdP
        raise OIDCError(f"could not fetch the IdP's {what} from {url}: {exc}")


def discovery() -> dict:
    issuer = settings.oidc_issuer.rstrip("/")
    now = time.monotonic()
    with _lock:
        cached = _discovery.get(issuer)
    if cached and now - cached[0] < DISCOVERY_TTL:
        return cached[1]
    try:
        doc = _fetch_json(issuer + "/.well-known/openid-configuration", "discovery document")
    except OIDCError:
        if cached:               # stale beats an IdP mid-restart
            return cached[1]
        raise
    for field in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(field):
            raise OIDCError(f"the IdP's discovery document has no {field}")
    if doc["issuer"].rstrip("/") != issuer:
        # RFC 8414: a document served for one issuer that names another is
        # exactly what a token-substitution setup looks like.
        raise OIDCError(
            f"discovery document names issuer {doc['issuer']}, expected {settings.oidc_issuer}")
    with _lock:
        _discovery[issuer] = (now, doc)
    return doc


def _jwks_keys(jwks_uri: str, force: bool = False) -> list[dict]:
    now = time.monotonic()
    with _lock:
        cached = _jwks.get(jwks_uri)
    if cached and not force and now - cached[0] < DISCOVERY_TTL:
        return cached[1].get("keys", [])
    try:
        doc = _fetch_json(jwks_uri, "signing keys (JWKS)")
    except OIDCError:
        if cached:
            return cached[1].get("keys", [])
        raise
    with _lock:
        _jwks[jwks_uri] = (now, doc)
    return doc.get("keys", [])


def _signing_key(jwks_uri: str, kid: str | None) -> dict:
    keys = _jwks_keys(jwks_uri)
    if kid:
        for key in keys:
            if key.get("kid") == kid:
                return key
        # Unknown kid usually means the IdP rotated its keys inside our TTL;
        # one forced refetch covers that without letting a garbage kid turn
        # into a fetch per request.
        for key in _jwks_keys(jwks_uri, force=True):
            if key.get("kid") == kid:
                return key
        raise OIDCError("the ID token is signed with a key the IdP does not publish")
    if not keys:
        raise OIDCError("the IdP publishes no signing keys")
    return {"keys": keys}        # no kid in the header: let jose try each


def _prune(store: dict[str, dict], ttl: float) -> None:
    now = time.monotonic()
    for k in [k for k, v in store.items() if now - v.get("created", 0) > ttl]:
        del store[k]
    if len(store) >= _MAX_PENDING:
        # Someone scripting GET /login can otherwise grow this without bound;
        # dropping every pending handshake costs a retried login, not memory.
        store.clear()


def begin(redirect_uri: str) -> str:
    """Start a login: remember state/nonce/verifier, return the IdP URL."""
    doc = discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)      # 64 chars, within RFC 7636's 43..128
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with _lock:
        _prune(_handshakes, HANDSHAKE_TTL)
        _handshakes[state] = {"nonce": nonce, "verifier": verifier,
                              "created": time.monotonic()}
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    return endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)


def pop_handshake(state: str) -> dict | None:
    """The nonce+verifier behind a state value -- once. A replayed or invented
    state gets None, which the callback treats as a refusal."""
    if not state:
        return None
    with _lock:
        rec = _handshakes.pop(state, None)
    if rec and time.monotonic() - rec["created"] <= HANDSHAKE_TTL:
        return rec
    return None


def exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    """Trade the authorization code for tokens at the IdP's token endpoint."""
    doc = discovery()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": verifier,
    }
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret
    try:
        r = httpx.post(doc["token_endpoint"], data=data, timeout=10)
    except Exception as exc:  # noqa: BLE001
        raise OIDCError(f"could not reach the IdP's token endpoint: {exc}")
    if r.status_code != 200:
        raise OIDCError(f"the IdP refused the code exchange (HTTP {r.status_code})")
    try:
        tokens = r.json()
    except ValueError:
        raise OIDCError("the IdP's token response was not JSON")
    if not tokens.get("id_token"):
        raise OIDCError("the IdP's token response carried no id_token")
    return tokens


def validate_id_token(id_token: str, nonce: str) -> dict:
    """The ID token's claims, after signature, iss, aud, exp and nonce all
    check out against the discovery document. Anything short of that raises."""
    doc = discovery()
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise OIDCError(f"malformed ID token: {exc}")
    alg = header.get("alg")
    if alg not in _ALGORITHMS:
        raise OIDCError(f"ID token uses refused algorithm {alg!r}")
    key = _signing_key(doc["jwks_uri"], header.get("kid"))
    try:
        claims = jwt.decode(
            id_token, key, algorithms=[alg],
            audience=settings.oidc_client_id, issuer=doc["issuer"],
            # No access token is passed in, so at_hash cannot be checked;
            # possession of the code + PKCE verifier already binds this
            # exchange to the browser that started it.
            options={"verify_at_hash": False})
    except JWTError as exc:
        raise OIDCError(f"ID token rejected: {exc}")
    if not nonce or claims.get("nonce") != nonce:
        raise OIDCError("ID token nonce does not match this login attempt")
    return claims


def role_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in settings.oidc_role_map.split(","):
        group, _, role = pair.strip().partition("=")
        if group and role in users.ROLES:
            out[group] = role
    return out


def resolve_role(claims: dict) -> str | None:
    """The local role the IdP's groups earn, or None for "refuse".

    A user in several mapped groups gets the highest role among them; a user
    in none gets OIDC_DEFAULT_ROLE, and its default of "deny" means refusal
    -- being known to the IdP is authentication, not authorization.
    """
    raw = claims.get(settings.oidc_role_claim)
    groups = [raw] if isinstance(raw, str) else [g for g in (raw or []) if isinstance(g, str)]
    mapping = role_map()
    mapped = [mapping[g] for g in groups if g in mapping]
    if mapped:
        return max(mapped, key=lambda r: users.ROLES.index(r))
    default = settings.oidc_default_role
    return default if default in users.ROLES else None


def username_from(claims: dict) -> str:
    """The local username an ID token claims: preferred_username, falling
    back to the email's local part. Lowercased here; users.upsert_sso runs
    it through the same validator every local username passes."""
    name = str(claims.get("preferred_username") or "").strip()
    if not name:
        name = str(claims.get("email") or "").strip().partition("@")[0]
    return name.lower()


def stash_session(token: str, username: str, role: str) -> str:
    """Park a freshly minted session behind a one-time code.

    The callback is a browser navigation, so the session token must not ride
    in it as a query parameter -- query strings land in every access log on
    the path. The browser instead gets this code in the URL *fragment* (never
    sent to servers) and trades it for the token with one POST.
    """
    code = secrets.token_urlsafe(32)
    with _lock:
        _prune(_handoffs, HANDOFF_TTL)
        _handoffs[code] = {"token": token, "username": username, "role": role,
                           "created": time.monotonic()}
    return code


def redeem(code: str) -> dict | None:
    if not code:
        return None
    with _lock:
        rec = _handoffs.pop(code, None)
    if rec and time.monotonic() - rec["created"] <= HANDOFF_TTL:
        return rec
    return None
