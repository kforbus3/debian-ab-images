"""Managing overlay.d from the browser, with the containment that implies.

Run it directly -- no pytest, no network:

    cd webui/backend && python tests/test_overlay.py

These paths arrive from a browser and end up at open(). Most of what is
asserted below is therefore not "does it save a file" but "does it refuse to
save one somewhere else": `..` segments, absolute paths that escape on join,
and symlinked directories pointing out of the tree. The UI drives the Docker
socket, so a write outside overlay.d is a write anywhere on the host.
"""
import io
import json
import os
import stat
import sys
import tempfile

PROJ = tempfile.mkdtemp()
os.makedirs(os.path.join(PROJ, "output"), exist_ok=True)
os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
                  ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient
from app.main import app
from app import orchestrator as orch

client = TestClient(app)
tok = client.post("/api/auth/login", data={"username": "admin", "password": "ci"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
ROOT = os.path.join(PROJ, "overlay.d")
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name} {extra}")


def put(path, content="hello\n", **kw):
    return client.put("/api/overlay/file", json={"path": path, "content": content, **kw}, headers=H)


print("\n== auth ==")
check("GET /overlay needs auth", client.get("/api/overlay").status_code == 401)
check("GET /overlay/file needs auth",
      client.get("/api/overlay/file", params={"path": "/x"}).status_code == 401)
check("PUT /overlay/file needs auth",
      client.put("/api/overlay/file", json={"path": "/x", "content": ""}).status_code == 401)
check("DELETE /overlay/file needs auth",
      client.delete("/api/overlay/file", params={"path": "/x"}).status_code == 401)
check("POST /overlay/upload needs auth",
      client.post("/api/overlay/upload", data={"path": "/x"},
                  files={"file": ("f", b"x")}).status_code == 401)

print("\n== writing ==")
r = put("/etc/hosts", "127.0.0.1 localhost\n")
check("a file can be created", r.status_code == 200, r.text)
check("...on disk where the builder will find it",
      open(os.path.join(ROOT, "etc/hosts")).read() == "127.0.0.1 localhost\n")
check("...with intermediate directories made", os.path.isdir(os.path.join(ROOT, "etc")))
check("...defaulting to 0644",
      oct(stat.S_IMODE(os.stat(os.path.join(ROOT, "etc/hosts")).st_mode)) == "0o644")

r = put("/usr/local/bin/site-check", "#!/bin/sh\n", executable=True)
check("executable=true gives 0755",
      oct(stat.S_IMODE(os.stat(os.path.join(ROOT, "usr/local/bin/site-check")).st_mode)) == "0o755", r.text)
r = put("/etc/secret.conf", "token=1\n", mode="0600")
check("an explicit octal mode is honoured",
      oct(stat.S_IMODE(os.stat(os.path.join(ROOT, "etc/secret.conf")).st_mode)) == "0o600", r.text)
check("a bad mode is refused", put("/etc/x", "y", mode="9999").status_code == 400)
check("a non-octal mode is refused", put("/etc/x", "y", mode="rwx").status_code == 400)

r = put("/etc/hosts", "127.0.0.1 localhost\n10.0.0.1 nas\n")
check("an existing file is replaced", "nas" in open(os.path.join(ROOT, "etc/hosts")).read(), r.text)

r = client.put("/api/overlay/file", json={"path": "/etc/hosts", "mode": "0640"}, headers=H)
check("mode changes without content", r.status_code == 200, r.text)
check("...and the content survives", "nas" in open(os.path.join(ROOT, "etc/hosts")).read())

print("\n== containment ==")
for bad in ("/../outside", "../outside", "/etc/../../outside", "/etc/./hosts",
            "/", "", "   ", "/etc/", "//..//outside"):
    r = put(bad, "x")
    check(f"refuses {bad!r}", r.status_code == 400, f"{r.status_code} {r.text[:80]}")
check("nothing escaped the tree", not os.path.exists(os.path.join(PROJ, "outside")))

# A symlinked directory is the case a textual path check misses entirely.
os.makedirs(os.path.join(ROOT, "opt"), exist_ok=True)
escape = os.path.join(ROOT, "opt", "escape")
if not os.path.lexists(escape):
    os.symlink(PROJ, escape)
r = put("/opt/escape/pwned", "x")
check("refuses a path through a symlink out of the tree", r.status_code == 400, r.text)
check("...and wrote nothing there", not os.path.exists(os.path.join(PROJ, "pwned")))
r = client.get("/api/overlay/file", params={"path": "/opt/escape/output/.secrets-store.json"}, headers=H)
check("...and cannot be read through either", r.status_code == 400, r.status_code)
os.unlink(escape)

r = put("/README.md", "x")
check("refuses the reserved README", r.status_code == 400, r.text)
check("...and says why", "documentation" in r.json()["detail"], r.text)

print("\n== listing ==")
open(os.path.join(ROOT, "README.md"), "w").write("docs\n")
files = client.get("/api/overlay", headers=H).json()
paths = [f["path"] for f in files["files"]]
check("lists what was written", "/etc/hosts" in paths and "/usr/local/bin/site-check" in paths, paths)
check("skips the README", "/README.md" not in paths, paths)
check("reports the mode", next(f for f in files["files"] if f["path"] == "/etc/secret.conf")["mode"] == "0600")
check("reports executability",
      next(f for f in files["files"] if f["path"] == "/usr/local/bin/site-check")["executable"] is True)
check("says it is writable", files["readonly_reason"] == "", files["readonly_reason"])

print("\n== reading ==")
r = client.get("/api/overlay/file", params={"path": "/etc/hosts"}, headers=H).json()
check("text comes back editable", r["editable"] is True and "nas" in r["content"], r)
with open(os.path.join(ROOT, "opt/blob.bin"), "wb") as f:
    f.write(bytes(range(256)))
r = client.get("/api/overlay/file", params={"path": "/opt/blob.bin"}, headers=H).json()
check("binary is not editable", r["editable"] is False and r["reason"] == "not text", r)
r = client.get("/api/overlay/download", params={"path": "/opt/blob.bin"}, headers=H)
check("...but downloads verbatim", r.content == bytes(range(256)), len(r.content))
with open(os.path.join(ROOT, "opt/big.txt"), "w") as f:
    f.write("x" * (1024 * 1024 + 10))
r = client.get("/api/overlay/file", params={"path": "/opt/big.txt"}, headers=H).json()
check("oversized text is not editable inline", r["editable"] is False and "larger" in r["reason"], r)
r = client.get("/api/overlay/file", params={"path": "/nope"}, headers=H)
check("a missing file is 404", r.status_code == 404, r.status_code)

print("\n== upload ==")
r = client.post("/api/overlay/upload",
                data={"path": "/opt/agent/agent.conf"},
                files={"file": ("agent.conf", io.BytesIO(b"key = value\n"))}, headers=H)
check("upload lands at the given path", r.status_code == 200, r.text)
check("...with the uploaded bytes",
      open(os.path.join(ROOT, "opt/agent/agent.conf")).read() == "key = value\n")
r = client.post("/api/overlay/upload", data={"path": "  "},
                files={"file": ("f", io.BytesIO(b"x"))}, headers=H)
check("upload with no path is refused", r.status_code == 400, r.text)
r = client.post("/api/overlay/upload", data={"path": "/../escaped"},
                files={"file": ("f", io.BytesIO(b"x"))}, headers=H)
check("upload cannot traverse", r.status_code == 400, r.text)
r = client.post("/api/overlay/upload", data={"path": "/usr/local/bin/tool", "executable": "true"},
                files={"file": ("tool", io.BytesIO(b"#!/bin/sh\n"))}, headers=H)
check("uploads can be executable",
      oct(stat.S_IMODE(os.stat(os.path.join(ROOT, "usr/local/bin/tool")).st_mode)) == "0o755", r.text)

print("\n== move and delete ==")
r = client.post("/api/overlay/move", json={"from": "/etc/secret.conf", "to": "/etc/app/secret.conf"}, headers=H)
check("a file can be moved", r.status_code == 200, r.text)
check("...to its new path", os.path.isfile(os.path.join(ROOT, "etc/app/secret.conf")))
check("...leaving nothing behind", not os.path.exists(os.path.join(ROOT, "etc/secret.conf")))
r = client.post("/api/overlay/move", json={"from": "/etc/app/secret.conf", "to": "/etc/hosts"}, headers=H)
check("a move onto an existing file is refused", r.status_code == 400, r.text)
r = client.post("/api/overlay/move", json={"from": "/etc/app/secret.conf", "to": "/../out"}, headers=H)
check("a move cannot traverse", r.status_code == 400, r.text)

# Deleting the last file in a directory must take the directory with it: cp -a
# would otherwise create an empty /etc/netplan in every image, which for netplan
# means a machine that boots with no network configuration at all.
put("/etc/netplan/10-corp.yaml", "network: {}\n")
r = client.delete("/api/overlay/file", params={"path": "/etc/netplan/10-corp.yaml"}, headers=H)
check("a file can be deleted", r.status_code == 200, r.text)
check("...and its emptied directory is pruned", not os.path.exists(os.path.join(ROOT, "etc/netplan")))
check("...but populated parents are kept", os.path.isdir(os.path.join(ROOT, "etc")))
check("deleting a missing file is 404",
      client.delete("/api/overlay/file", params={"path": "/etc/gone"}, headers=H).status_code == 404)
check("delete cannot traverse",
      client.delete("/api/overlay/file", params={"path": "/../../etc/passwd"}, headers=H).status_code == 400)

print("\n== read-only mount ==")
if os.geteuid() == 0:
    print("  SKIP  running as root; a read-only directory is not read-only to root")
else:
    mode = os.stat(ROOT).st_mode
    os.chmod(ROOT, 0o555)
    try:
        r = client.get("/api/overlay", headers=H).json()
        check("says so, with the fix", "not writable" in r["readonly_reason"], r["readonly_reason"])
        # A top-level path, deliberately: only overlay.d itself was made
        # read-only, and its existing subdirectories still carry their own
        # permissions -- so /etc/anything would still be writable here.
        r = put("/newtop.conf", "x")
        check("...and a write there fails with a message, not a stack trace",
              r.status_code == 500 and "overlay.d" in r.json()["detail"], r.text)
    finally:
        os.chmod(ROOT, stat.S_IMODE(mode))

print("\n== the build still sees them ==")
files = client.get("/api/overlay", headers=H).json()
check("the builder's listing and the UI's are the same call", files["count"] == len(files["files"]))
check("host path still reported for the bind mount", "overlay.d" in files["dir"], files["dir"])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
