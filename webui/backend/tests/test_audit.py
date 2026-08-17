#!/usr/bin/env python3
"""Every change must leave a line saying who made it -- and the log itself
must never become a liability.

Three quiet failure shapes are pinned here: a mutating endpoint that leaves
no trace (the middleware missing a router, which is invisible until an
incident needs the log), a failed login that records the password someone
mistyped into the username's habits (the log must know who was tried, never
with what), and an unbounded file that fills the disk the builds need.
"""
import json
import os
import sys
import tempfile
import time

PROJ = tempfile.mkdtemp()
OUT = os.path.join(PROJ, "output")
os.makedirs(OUT, exist_ok=True)
os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
                  ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app                   # noqa: E402
from app import audit                      # noqa: E402

client = TestClient(app)
ok = fail = 0
APATH = os.path.join(OUT, "audit.jsonl")


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def rows():
    try:
        return [json.loads(line) for line in open(APATH)]
    except OSError:
        return []


tok = client.post("/api/auth/login",
                  data={"username": "admin", "password": "ci"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

print("== a login writes an entry ==")
logins = [r for r in rows() if r["path"] == "/api/auth/login" and r["status"] == 200]
check("success recorded with actor and role",
      any(r["actor"] == "admin" and r["role"] == "admin" for r in logins), logins)

print("== a failed login records the username, never the password ==")
client.post("/api/auth/login", data={"username": "mallory", "password": "s3cr3t-Attempt"})
failures = [r for r in rows() if r["path"] == "/api/auth/login" and r["status"] == 401]
check("the attempted username is kept", any(r["actor"] == "mallory" for r in failures),
      failures)
check("the password is nowhere in the log", "s3cr3t-Attempt" not in open(APATH).read())

print("== a mutating call is audited via the middleware ==")
before = len(rows())
r = client.post("/api/users", headers=H,
                json={"username": "auditee", "password": "longenough1", "role": "viewer"})
check("the call succeeded", r.status_code == 200, r.text)
made = [r for r in rows() if r["method"] == "POST" and r["path"] == "/api/users"]
check("one entry, naming the actor",
      any(r["actor"] == "admin" and r["status"] == 200 for r in made), made)
check("with the endpoint's own summary",
      any("auditee" in r.get("summary", "") for r in made), made)
check("exactly one entry for the call", len(rows()) == before + 1,
      f"{len(rows())} vs {before}+1")

print("== an anonymous knock on a mutating endpoint is a line too ==")
client.post("/api/server/up")
knocks = [r for r in rows() if r["path"] == "/api/server/up"]
check("recorded with actor '-' and its 401",
      any(r["actor"] == "-" and r["status"] == 401 for r in knocks), knocks)

print("== the machine endpoints do not flood the log ==")
before = len(rows())
for i in range(5):
    client.post("/api/imaging/report", data={"id": f"m{i}", "phase": "writing"})
    client.post("/api/imaging/checkin", data={"id": f"m{i}"})
check("ten machine reports, zero audit lines", len(rows()) == before,
      f"{len(rows())} vs {before}")

print("== the LUKS passphrase reveal is audited, though it is a GET ==")
r = client.get("/api/secrets/passphrase/some.img", headers=H)
reveals = [r for r in rows() if "passphrase" in r["path"]]
check("the attempt is recorded", bool(reveals), reveals)
check("with its outcome", any(r["status"] == 400 for r in reveals), reveals)
check("and what was asked for", any("some.img" in r.get("summary", "") for r in reveals))

print("== GET /api/audit answers newest first, admin only ==")
r = client.get("/api/audit", headers=H)
events = r.json()["events"]
check("newest first", events == sorted(events, key=lambda e: e["ts"], reverse=True))
cutoff = events[2]["ts"]
since = client.get(f"/api/audit?since={cutoff}", headers=H).json()["events"]
check("since filters strictly after", all(e["ts"] > cutoff for e in since) and since)
check("limit caps the answer",
      len(client.get("/api/audit?limit=3", headers=H).json()["events"]) == 3)
check("actor filters",
      all(e["actor"] == "mallory"
          for e in client.get("/api/audit?actor=mallory", headers=H).json()["events"]))

print("== the file stays bounded ==")
# Same trim shape as deployments.jsonl, with the constants shrunk so the test
# does not need to write 20k real requests.
audit.MAX_EVENTS, audit.TRIM_TO = 50, 30
for i in range(300):
    audit.record(actor="bulk", role="viewer", method="POST", path="/api/x",
                 status=200, ip="1.2.3.4", summary=f"row {i}")
n = len(rows())
check("trimmed oldest-first past the cap", n <= 50, n)
check("the newest rows survive", rows()[-1]["summary"] == "row 299", rows()[-1])

print("== recording never raises, even with the directory gone ==")
audit._path_orig = audit._path
audit._path = lambda: "/nonexistent-dir/audit.jsonl"
try:
    audit.record(actor="x", role="", method="POST", path="/y", status=200)
    check("an unwritable log does not fail the request", True)
except Exception as exc:  # noqa: BLE001
    check("an unwritable log does not fail the request", False, exc)
finally:
    audit._path = audit._path_orig

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
