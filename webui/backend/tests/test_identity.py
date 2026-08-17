#!/usr/bin/env python3
"""Named users, roles, sessions and API tokens must not break a deployment.

The single shared password had exactly one compatibility property worth
keeping: log in as admin with ADMIN_PASSWORD and everything works. These
assert that property survives, and that the new machinery actually delivers
what it exists for -- because every failure here has a quiet production
shape: a bootstrap that writes the password in plaintext, an env var that
silently reverts a changed password on the next redeploy, a viewer who can
start builds, a revoked session that keeps working, a "one-time" token that
can be read back out of a state file.
"""
import json
import os
import sys
import tempfile
import time

PROJ = tempfile.mkdtemp()
OUT = os.path.join(PROJ, "output")
os.makedirs(OUT, exist_ok=True)
BOOT_PW = "ci-bootstrap-pw"
os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
                  ADMIN_PASSWORD=BOOT_PW, SECRET_KEY="ci-secret-key")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app                   # noqa: E402
from app import apitokens, sessions, users # noqa: E402

client = TestClient(app)
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def login(username, password):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    return r, ({"Authorization": f"Bearer {r.json()['access_token']}"}
               if r.status_code == 200 else None)


print("== unauthenticated requests still answer 401, open endpoints stay open ==")
# The CI auth sweep and the nginx route test both depend on the 401s; the
# machines being imaged depend on the two open POSTs.
for path in ("/api/imaging", "/api/deployments", "/api/images", "/api/bundles",
             "/api/overlay", "/api/jobs", "/api/users", "/api/tokens",
             "/api/audit", "/api/auth/sessions", "/api/secrets/config"):
    check(f"GET {path} -> 401", client.get(path).status_code == 401,
          client.get(path).status_code)
r = client.post("/api/imaging/report", data={"id": "aa:bb", "phase": "writing"})
check("imaging/report stays open", r.status_code == 200, r.status_code)
r = client.post("/api/imaging/checkin", data={"id": "aa:bb"})
check("imaging/checkin stays open", r.status_code == 200, r.status_code)

print("== first boot bootstraps admin from ADMIN_PASSWORD, hashed ==")
r, H = login("admin", BOOT_PW)
check("admin/<ADMIN_PASSWORD> logs in exactly as before", r.status_code == 200, r.text)
check("the session token is opaque, not a JWT", r.json()["access_token"].startswith("fls_"))
check("login reports the admin role", r.json().get("role") == "admin")
upath = os.path.join(OUT, "users.json")
check("users.json exists", os.path.exists(upath))
check("users.json is 0600", oct(os.stat(upath).st_mode & 0o777) == "0o600",
      oct(os.stat(upath).st_mode & 0o777))
raw = open(upath).read()
check("the password is not on disk in plaintext", BOOT_PW not in raw)
check("the hash is bcrypt", json.loads(raw)["users"][0]["password_hash"].startswith("$2"))
r = client.get("/api/auth/check", headers=H)
check("auth check names the user and role",
      r.json() == {"ok": True, "username": "admin", "role": "admin"}, r.text)
r, _ = login("admin", "wrong-password")
check("a wrong password is refused", r.status_code == 401)

print("== a stolen state file yields no working session ==")
# Only hashes are stored; presenting one as a token must fail.
stored = json.load(open(os.path.join(OUT, ".sessions.json")))
sha = next(iter(stored["sessions"]))
check("the sessions file holds hashes, not tokens", not sha.startswith("fls_"))
r = client.get("/api/auth/check", headers={"Authorization": f"Bearer fls_{sha}"})
check("a hash presented as a token is refused", r.status_code == 401)

print("== a pre-upgrade JWT is refused cleanly, not a 500 ==")
from jose import jwt as _jwt  # noqa: E402
old = _jwt.encode({"sub": "admin", "scope": "session", "exp": time.time() + 3600},
                  "ci-secret-key", algorithm="HS256")
r = client.get("/api/images", headers={"Authorization": f"Bearer {old}"})
check("old-style JWT -> 401", r.status_code == 401, r.status_code)

print("== a changed password is NOT reverted by the env var ==")
r = client.patch("/api/users/admin", headers=H, json={"password": "changed-by-ui-1"})
check("admin can change a password", r.status_code == 200, r.text)
r = client.get("/api/auth/check", headers=H)
check("a password reset revokes the user's sessions", r.status_code == 401, r.status_code)
# What a restart does: the file exists, so ADMIN_PASSWORD must be ignored.
users.load()
r, _ = login("admin", BOOT_PW)
check("the env-var password no longer works", r.status_code == 401)
r, H = login("admin", "changed-by-ui-1")
check("the UI-set password does", r.status_code == 200, r.text)

print("== RBAC: the matrix holds per role ==")
for name, role in (("op1", "operator"), ("view1", "viewer")):
    r = client.post("/api/users", headers=H,
                    json={"username": name, "password": "longenough1", "role": role})
    check(f"create {role} {name}", r.status_code == 200, r.text)
_, OH = login("op1", "longenough1")
_, VH = login("view1", "longenough1")

# viewer: reads work, every mutation is 403 -- authenticated but insufficient,
# which must not be 401 or the UI would bounce them to a login that fixes nothing
check("viewer GET /api/images -> 200", client.get("/api/images", headers=VH).status_code == 200)
check("viewer GET /api/overlay -> 200", client.get("/api/overlay", headers=VH).status_code == 200)
check("viewer GET /api/jobs -> 200", client.get("/api/jobs", headers=VH).status_code == 200)
for method, path in (("POST", "/api/builds"), ("DELETE", "/api/imaging/aa:bb"),
                     ("PUT", "/api/overlay/file"), ("POST", "/api/bundles/build"),
                     ("POST", "/api/server/up"), ("DELETE", "/api/images/x.img")):
    r = client.request(method, path, headers=VH, json={})
    check(f"viewer {method} {path} -> 403", r.status_code == 403, r.status_code)

# operator: operations allowed, administration is not
r = client.put("/api/overlay/file", headers=OH,
               json={"path": "/etc/rbac-test", "content": "x\n"})
check("operator can write overlay files", r.status_code == 200, r.text)
check("operator can forget an imaging row",
      client.delete("/api/imaging/aa:bb", headers=OH).status_code == 200)
for method, path in (("POST", "/api/users"), ("POST", "/api/tokens"),
                     ("GET", "/api/audit"), ("GET", "/api/secrets/config"),
                     ("PUT", "/api/server/config"), ("GET", "/api/auth/sessions")):
    r = client.request(method, path, headers=OH, json={})
    check(f"operator {method} {path} -> 403", r.status_code == 403, r.status_code)

# admin: the administrative surface answers
check("admin GET /api/users -> 200", client.get("/api/users", headers=H).status_code == 200)
check("admin GET /api/audit -> 200", client.get("/api/audit", headers=H).status_code == 200)
check("admin GET /api/secrets/config -> 200",
      client.get("/api/secrets/config", headers=H).status_code == 200)
check("admin GET /api/auth/sessions -> 200",
      client.get("/api/auth/sessions", headers=H).status_code == 200)

print("== session revocation is immediate ==")
_, DH = login("view1", "longenough1")
check("the doomed session works", client.get("/api/auth/check", headers=DH).status_code == 200)
sess = client.get("/api/auth/sessions", headers=H).json()["sessions"]
target = [s for s in sess if s["username"] == "view1"]
check("admin sees the session, id only", bool(target) and len(target[0]["id"]) == 12)
for s in target:
    client.delete(f"/api/auth/sessions/{s['id']}", headers=H)
check("revoked by an admin -> dead on the next request",
      client.get("/api/auth/check", headers=DH).status_code == 401)
_, DH = login("view1", "longenough1")
client.post("/api/auth/logout", headers=DH)
check("logout revokes server-side, not just in the browser",
      client.get("/api/auth/check", headers=DH).status_code == 401)

print("== sessions survive a backend restart ==")
sessions._cache = None            # what a restart does to the in-memory view
check("a session from before the restart still resolves",
      client.get("/api/auth/check", headers=H).status_code == 200)

print("== sliding expiry refreshes on use, capped at 7 days ==")
raw_tok = sessions.create("view1", ip="t")
rec = sessions._load()[sessions._sha(raw_tok)]
rec["expires"] = time.time() + 60           # nearly idle-expired
before = rec["expires"]
resolved = sessions.resolve(raw_tok)
check("use pushes the deadline out", resolved and resolved["expires"] > before + 3600,
      resolved and resolved["expires"] - before)
rec["created"] = time.time() - sessions.MAX_SECONDS - 1
check("the 7-day absolute cap wins over sliding refresh",
      sessions.resolve(raw_tok) is None)
rec2 = sessions._load().get(sessions._sha(raw_tok))
if rec2 is not None:
    rec2["created"] = time.time() - sessions.MAX_SECONDS + 30
    got = sessions.resolve(raw_tok)
    check("near the cap, refresh cannot slide past it",
          got is not None and got["expires"] <= got["created"] + sessions.MAX_SECONDS + 1,
          got)

print("== disabling a user ends their access now ==")
_, DH = login("view1", "longenough1")
client.patch("/api/users/view1", headers=H, json={"disabled": True})
check("disabled -> the live session is dead",
      client.get("/api/auth/check", headers=DH).status_code == 401)
r, _ = login("view1", "longenough1")
check("disabled -> no new login either", r.status_code == 401)
client.patch("/api/users/view1", headers=H, json={"disabled": False})

print("== the last enabled admin cannot be removed ==")
for body, what in ((None, "delete"), ({"role": "viewer"}, "demote"),
                   ({"disabled": True}, "disable")):
    if body is None:
        r = client.delete("/api/users/admin", headers=H)
    else:
        r = client.patch("/api/users/admin", headers=H, json=body)
    check(f"{what} the last admin -> refused", r.status_code == 400, r.status_code)
    check(f"the {what} refusal says why", "last enabled admin" in r.json()["detail"],
          r.json()["detail"])
client.post("/api/users", headers=H,
            json={"username": "admin2", "password": "longenough1", "role": "admin"})
r = client.patch("/api/users/admin2", headers=H, json={"role": "operator"})
check("with a second admin, demoting one is fine", r.status_code == 200, r.text)
client.patch("/api/users/admin2", headers=H, json={"role": "admin"})
r = client.delete("/api/users/admin2", headers=H)
check("with a second admin, deleting one is fine", r.status_code == 200, r.text)

print("== usernames are validated, not corrected ==")
for bad in ("Admin", "a b", "x" * 33, "", "sneaky/../path"):
    r = client.post("/api/users", headers=H,
                    json={"username": bad, "password": "longenough1", "role": "viewer"})
    # "Admin" folds to the existing admin -> duplicate; both are 400.
    check(f"username {bad!r} refused", r.status_code == 400, r.status_code)

print("== API tokens: shown once, stored hashed, revocable ==")
r = client.post("/api/tokens", headers=H, json={"name": "ci-token", "role": "operator"})
check("create returns the raw token once", r.status_code == 200 and
      r.json()["token"].startswith("flt_"), r.text)
flt = r.json()["token"]
tid = r.json()["id"]
tfile = open(os.path.join(OUT, ".api-tokens.json")).read()
check("the raw token is not on disk", flt not in tfile)
check("its sha256 is", apitokens._sha(flt) in tfile)
listing = client.get("/api/tokens", headers=H).json()["tokens"]
check("the listing never contains the token",
      flt not in json.dumps(listing) and all(len(t["id"]) == 12 for t in listing))
TH = {"Authorization": f"Bearer {flt}"}
check("the token authenticates via Bearer",
      client.get("/api/jobs", headers=TH).status_code == 200)
check("with its own role, not its creator's",
      client.get("/api/users", headers=TH).status_code == 403)
check("and it can do operator work",
      client.delete("/api/imaging/none", headers=TH).status_code == 200)
r = client.delete(f"/api/tokens/{tid}", headers=H)
check("revoke answers", r.status_code == 200, r.text)
check("a revoked token is dead immediately",
      client.get("/api/jobs", headers=TH).status_code == 401)

print("== a token's role cannot exceed its creator's ==")
# Unreachable over HTTP today (creation is admin-only, and admin is the
# ceiling), but the check must hold the day token creation is opened wider.
try:
    apitokens.create("escalate", "admin", "op1", creator_role="operator")
    check("operator minting an admin token -> refused", False, "it was created")
except apitokens.TokenError as exc:
    check("operator minting an admin token -> refused", "exceed" in str(exc), exc)

print("== token expiry is enforced ==")
raw_t, rec_t = apitokens.create("shortlived", "viewer", "admin", "admin",
                                expires_days=1)
for t in apitokens._load():
    if t["name"] == "shortlived":
        t["expires"] = time.time() - 1
check("an expired token no longer resolves", apitokens.resolve(raw_t) is None)

print("== the stream-token mechanism still works ==")
from app.security import create_stream_token, verify_stream_token  # noqa: E402
st = create_stream_token("job-1", subject="op1")
try:
    verify_stream_token(st, "job-1")
    check("a minted stream token verifies for its job", True)
except Exception as exc:  # noqa: BLE001
    check("a minted stream token verifies for its job", False, exc)
try:
    verify_stream_token(st, "job-2")
    check("but not for another job", False)
except Exception:  # noqa: BLE001
    check("but not for another job", True)
check("the SSE endpoint refuses a bad token",
      client.get("/api/jobs/job-1/stream?token=garbage").status_code == 401)

print("== failed logins are throttled per username+IP ==")
for _ in range(5):
    login("hammered", "wrong")
r, _ = login("hammered", "wrong")
check("the hammered pair is throttled", r.status_code == 429, r.status_code)
r, _ = login("someone-else", "wrong")
check("a different username from the same IP is not", r.status_code == 401, r.status_code)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
