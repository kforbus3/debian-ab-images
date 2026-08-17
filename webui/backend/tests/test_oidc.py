"""OIDC single sign-on, end to end against a fake IdP.

Run it directly -- no pytest, no network beyond loopback:

    cd webui/backend && python tests/test_oidc.py

The fake IdP is the real thing's shape: a discovery document, a JWKS with a
generated RSA key, an authorization endpoint that redirects back with a code,
and a token endpoint that checks the PKCE verifier and answers with a signed
ID token. Everything else is the real app via TestClient, so what is asserted
is the wiring that matters: a login's state/nonce/PKCE actually binding the
callback to the browser that started it, group claims becoming roles, and --
above all -- the refusals. An SSO bug's production shape is not an error
message; it is a stranger holding a session, or an IdP username quietly
taking over a local account.
"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJ = tempfile.mkdtemp()
os.makedirs(os.path.join(PROJ, "output"), exist_ok=True)

from cryptography.hazmat.primitives import serialization                    # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa                   # noqa: E402


def _rsa_pems():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PRIV_PEM, PUB_PEM = _rsa_pems()
EVIL_PRIV, _ = _rsa_pems()          # a different key, same kid: forged signature
KID = "test-key-1"

# Knobs the cases below turn between logins; the token endpoint reads them.
CTRL = {"username": "alice", "email": "alice@example.com",
        "groups": ["flipside-ops"], "nonce_override": None,
        "exp_offset": 600, "aud": "flipside", "sign_pem": PRIV_PEM}
AUTH_CODES = {}                     # code -> {nonce, challenge}
TOKEN_REQS = []                     # every form body the token endpoint saw


class IdP(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/.well-known/openid-configuration":
            return self._send(200, {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "jwks_uri": ISSUER + "/jwks",
            })
        if path == "/jwks":
            return self._send(200, {"keys": [PUB_JWK]})
        if path == "/authorize":
            q = dict(urllib.parse.parse_qsl(query))
            code = "code-" + str(len(AUTH_CODES))
            AUTH_CODES[code] = {"nonce": q.get("nonce", ""),
                                "challenge": q.get("code_challenge", "")}
            back = (q["redirect_uri"] + "?" +
                    urllib.parse.urlencode({"code": code, "state": q.get("state", "")}))
            self.send_response(302)
            self.send_header("Location", back)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        TOKEN_REQS.append(form)
        if self.path != "/token":
            return self._send(404, {})
        issued = AUTH_CODES.pop(form.get("code", ""), None)
        if issued is None or form.get("grant_type") != "authorization_code":
            return self._send(400, {"error": "invalid_grant"})
        # PKCE for real: the verifier must hash to the challenge the
        # authorization request carried, or there is no exchange.
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != issued["challenge"]:
            return self._send(400, {"error": "invalid_grant",
                                    "error_description": "PKCE verification failed"})
        now = time.time()
        claims = {"iss": ISSUER, "aud": CTRL["aud"], "sub": "sub-" + (CTRL["username"] or "x"),
                  "iat": now, "exp": now + CTRL["exp_offset"],
                  "nonce": CTRL["nonce_override"] or issued["nonce"],
                  "email": CTRL["email"], "groups": CTRL["groups"]}
        if CTRL["username"]:
            claims["preferred_username"] = CTRL["username"]
        id_token = jose_jwt.encode(claims, CTRL["sign_pem"], algorithm="RS256",
                                   headers={"kid": KID})
        return self._send(200, {"access_token": "at-x", "token_type": "Bearer",
                                "id_token": id_token})


srv = HTTPServer(("127.0.0.1", 0), IdP)
ISSUER = f"http://127.0.0.1:{srv.server_port}"
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ.update(
    PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
    ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key",
    OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID="flipside",
    OIDC_ROLE_MAP="flipside-admins=admin,flipside-ops=operator,flipside-view=viewer",
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import httpx                                      # noqa: E402
from jose import jwk as jose_jwk                  # noqa: E402
from jose import jwt as jose_jwt                  # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402
from app.main import app                          # noqa: E402
from app import audit, oidc                       # noqa: E402
from app.config import settings                   # noqa: E402

PUB_JWK = {**jose_jwk.construct(PUB_PEM, "RS256").to_dict(), "kid": KID, "use": "sig"}

client = TestClient(app)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name} {extra}")


def sso_roundtrip():
    """Browser's-eye view: /login redirect -> IdP authorize -> callback."""
    start = client.get("/api/auth/oidc/login", follow_redirects=False)
    if start.status_code != 302:
        return start, None, None
    authz = start.headers["location"]
    idp = httpx.get(authz)                       # httpx does not follow redirects
    back = idp.headers["location"]               # http://testserver/api/auth/oidc/callback?...
    cb = client.get(back[back.index("/api"):], follow_redirects=False)
    return start, authz, cb


def fragment(resp):
    return dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(resp.headers.get("location", "")).fragment))


def last_audit(needle):
    return next((e for e in audit.events(limit=50) if needle in e.get("summary", "")), None)


print("== the login page is told what to render ==")
r = client.get("/api/auth/methods")
check("methods is open", r.status_code == 200, r.status_code)
check("password is always offered", r.json()["password"] is True)
check("oidc enabled with its display name",
      r.json()["oidc"] == {"enabled": True, "display_name": "Single sign-on"}, r.json())

print("== happy path: PKCE code flow end to end ==")
start, authz, cb = sso_roundtrip()
q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authz).query))
check("redirects to the IdP's authorization endpoint", authz.startswith(ISSUER + "/authorize"))
check("carries state", len(q.get("state", "")) >= 16, q)
check("carries a nonce", len(q.get("nonce", "")) >= 16, q)
check("carries a PKCE challenge, S256",
      q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) >= 40, q)
check("redirect_uri is derived from the request's own origin",
      q.get("redirect_uri") == "http://testserver/api/auth/oidc/callback", q)
check("scopes default to openid profile email", q.get("scope") == "openid profile email", q)
check("callback lands back on the login page", cb.status_code == 302
      and cb.headers["location"].startswith("/login#sso="), cb.headers.get("location"))
check("no client_secret sent for a public client",
      "client_secret" not in TOKEN_REQS[-1], TOKEN_REQS[-1])
handoff = fragment(cb)["sso"]
r = client.post("/api/auth/oidc/exchange", json={"code": handoff})
check("exchange answers the same shape as password login",
      r.status_code == 200 and r.json()["token_type"] == "bearer", r.text)
check("...an opaque fls_ session", r.json()["access_token"].startswith("fls_"))
check("...with the mapped role", r.json() | {"access_token": ""} == {
      "access_token": "", "token_type": "bearer", "username": "alice", "role": "operator"}, r.json())
ALICE = {"Authorization": f"Bearer {r.json()['access_token']}"}
check("the session works on a protected endpoint",
      client.get("/api/images", headers=ALICE).status_code == 200)
check("...at the operator rank, not more",
      client.get("/api/users", headers=ALICE).status_code == 403)
r2 = client.post("/api/auth/oidc/exchange", json={"code": handoff})
check("the handoff code is one-time", r2.status_code == 401, r2.status_code)
check("the login is audited", last_audit("logged in via SSO") is not None)

users_file = json.load(open(os.path.join(PROJ, "output", "users.json")))
alice = next(u for u in users_file["users"] if u["username"] == "alice")
check("the record carries no password hash", "password_hash" not in alice, alice)
check("...and names its source", alice.get("source") == "oidc", alice)

print("== the handshake binds the callback to the login that started it ==")
r = client.get("/api/auth/oidc/callback?code=x&state=never-issued", follow_redirects=False)
check("an invented state is refused", r.status_code == 400, r.status_code)
start = client.get("/api/auth/oidc/login", follow_redirects=False)
authz = start.headers["location"]
back = httpx.get(authz).headers["location"]
path = back[back.index("/api"):]
first = client.get(path, follow_redirects=False)
replay = client.get(path, follow_redirects=False)
check("a state cannot be replayed", first.status_code == 302 and replay.status_code == 400,
      (first.status_code, replay.status_code))

CTRL["nonce_override"] = "not-the-nonce"
_, _, cb = sso_roundtrip()
check("a nonce mismatch is refused", cb.status_code == 401, cb.status_code)
CTRL["nonce_override"] = None

CTRL["sign_pem"] = EVIL_PRIV
_, _, cb = sso_roundtrip()
check("a forged signature is refused", cb.status_code == 401, cb.status_code)
CTRL["sign_pem"] = PRIV_PEM

CTRL["exp_offset"] = -120
_, _, cb = sso_roundtrip()
check("an expired ID token is refused", cb.status_code == 401, cb.status_code)
CTRL["exp_offset"] = 600

CTRL["aud"] = "someone-elses-client"
_, _, cb = sso_roundtrip()
check("a token for another client is refused", cb.status_code == 401, cb.status_code)
CTRL["aud"] = "flipside"

print("== groups become roles ==")
CTRL.update(username="bob", email="bob@example.com",
            groups=["flipside-view", "flipside-admins", "unrelated"])
_, _, cb = sso_roundtrip()
r = client.post("/api/auth/oidc/exchange", json={"code": fragment(cb)["sso"]})
check("several mapped groups -> the highest role", r.json().get("role") == "admin", r.json())

CTRL["groups"] = ["flipside-view"]
_, _, cb = sso_roundtrip()
r = client.post("/api/auth/oidc/exchange", json={"code": fragment(cb)["sso"]})
check("a group change propagates on the next login",
      r.json().get("role") == "viewer", r.json())

r = client.post("/api/auth/login", data={"username": "admin", "password": "ci"})
ADMIN = {"Authorization": f"Bearer {r.json()['access_token']}"}
listed = {u["username"]: u for u in client.get("/api/users", headers=ADMIN).json()["users"]}
check("the users page sees the updated role", listed["bob"]["role"] == "viewer", listed.get("bob"))

CTRL.update(username="mallory", groups=["nothing-mapped"])
_, _, cb = sso_roundtrip()
check("unmapped groups + default deny -> refused, back at the login page",
      cb.status_code == 302 and "sso_error=" in cb.headers["location"], cb.headers.get("location"))
check("...the message says why",
      "map to a role" in urllib.parse.unquote(cb.headers["location"]), cb.headers["location"])
refusal = last_audit("map to a role") or {}
check("...and the refusal is audited as a 403",
      refusal.get("status") == 403 and refusal.get("actor") == "mallory", refusal)
check("...and no user record was created",
      "mallory" not in {u["username"] for u in client.get("/api/users", headers=ADMIN).json()["users"]})

settings.oidc_default_role = "viewer"
_, _, cb = sso_roundtrip()
r = client.post("/api/auth/oidc/exchange", json={"code": fragment(cb)["sso"]})
check("OIDC_DEFAULT_ROLE=viewer admits the same user at viewer",
      r.status_code == 200 and r.json()["role"] == "viewer", r.text)
settings.oidc_default_role = "deny"

print("== local protections hold against a valid IdP assertion ==")
r = client.patch("/api/users/bob", headers=ADMIN, json={"disabled": True})
check("admin can disable the SSO user", r.status_code == 200, r.text)
CTRL.update(username="bob", groups=["flipside-view"])
_, _, cb = sso_roundtrip()
check("a locally-disabled user is refused despite valid SSO",
      cb.status_code == 302 and "sso_error=" in cb.headers["location"], cb.headers.get("location"))
check("...audited", (last_audit("disabled") or {}).get("actor") == "bob")
client.patch("/api/users/bob", headers=ADMIN, json={"disabled": False})

r = client.patch("/api/users/bob", headers=ADMIN, json={"password": "longenough1"})
check("an SSO user cannot be given a password",
      r.status_code == 400 and "signs in via oidc" in r.json()["detail"], r.text)

client.post("/api/users", headers=ADMIN,
            json={"username": "sam", "password": "longenough1", "role": "admin"})
CTRL.update(username="sam", groups=["flipside-view"])
_, _, cb = sso_roundtrip()
check("an IdP username colliding with a local user is refused, not merged",
      cb.status_code == 302 and "sso_error=" in cb.headers["location"], cb.headers.get("location"))
sam = next(u for u in json.load(open(os.path.join(PROJ, "output", "users.json")))["users"]
           if u["username"] == "sam")
check("...the local record is untouched",
      sam.get("source") == "local" and sam.get("password_hash", "").startswith("$2")
      and sam.get("role") == "admin", sam)
r = client.post("/api/auth/login", data={"username": "sam", "password": "longenough1"})
check("...and the local sam still logs in with their password", r.status_code == 200, r.text)
check("...the collision is audited",
      "already belongs" in (last_audit("already belongs") or {}).get("summary", ""))

print("== username fallback: the email's local part ==")
CTRL.update(username="", email="Eve@Example.COM", groups=["flipside-view"])
_, _, cb = sso_roundtrip()
r = client.post("/api/auth/oidc/exchange", json={"code": fragment(cb)["sso"]})
check("no preferred_username -> lowercased email local part",
      r.status_code == 200 and r.json()["username"] == "eve", r.text)
CTRL.update(username="alice", email="alice@example.com")

print("== a down IdP breaks only the SSO button ==")
settings.oidc_issuer = "http://127.0.0.1:1"
oidc._discovery.clear()
r = client.get("/api/auth/oidc/login", follow_redirects=False)
check("SSO login fails with a clear 502", r.status_code == 502, r.status_code)
r = client.post("/api/auth/login", data={"username": "admin", "password": "ci"})
check("password login does not care", r.status_code == 200, r.status_code)

print("== unconfigured: the feature does not exist ==")
settings.oidc_issuer = ""
r = client.get("/api/auth/methods")
check("methods says oidc is off", r.json()["oidc"]["enabled"] is False, r.json())
check("the login endpoint 404s",
      client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404)
check("the callback 404s",
      client.get("/api/auth/oidc/callback?code=x&state=y",
                 follow_redirects=False).status_code == 404)
check("the exchange 404s",
      client.post("/api/auth/oidc/exchange", json={"code": "x"}).status_code == 404)
r = client.post("/api/auth/login", data={"username": "admin", "password": "ci"})
check("password login is untouched", r.status_code == 200, r.status_code)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
