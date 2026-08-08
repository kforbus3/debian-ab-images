#!/usr/bin/env python3
"""The SPA catch-all must not answer for paths it does not own.

This is not a cosmetic 404. The catch-all returned index.html with a 200 for
*any* unknown path, so a machine pointed at the web UI's port instead of the
provisioning server downloaded the React app under a .raucb name. RAUC streamed
it, read the last eight bytes of the page as the bundle's signature size, and
reported:

    Invalid bundle format: Signature size (4336799815442382346) exceeds bundle size

4336799815442382346 is 0x3c2f68746d6c3e0a -- "</html>\\n". Nothing in that error
mentions a wrong port, and the advice printed under it was about signing keys,
so the first two people to read it went and checked a certificate.

A 404 costs nothing and makes the mistake legible at the moment it is made.
"""
import os
import sys
import tempfile

# Set before importing the app: it resolves its state directory at import time.
PROJ = tempfile.mkdtemp()
os.makedirs(os.path.join(PROJ, "output"), exist_ok=True)

# A real built frontend, so the 404s below are shown to be about the path rather
# than about there being nothing to serve -- and so the one thing this must not
# break, serving files that do exist, is actually exercised.
STATIC = tempfile.mkdtemp()
with open(os.path.join(STATIC, "index.html"), "w") as fh:
    fh.write("<!doctype html><html><body>app</body></html>\n")
with open(os.path.join(STATIC, "favicon.ico"), "w") as fh:
    fh.write("icon")

os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR=STATIC,
                  ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

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


print("== paths owned by the provisioning server ==")
for path in ("/bundles/debian-ab-2026.08.08.raucb",
             "/bundles/latest",
             "/images/debian-trixie-ab.img.zst",
             "/imager/vmlinuz",
             "/hosts/01-aa-bb-cc-dd-ee-ff.ipxe"):
    r = client.get(path)
    check(f"404 for {path}", r.status_code == 404, f"got {r.status_code}")
    check(f"no HTML body for {path}", "<html" not in r.text.lower(),
          "answered with a page")

r = client.get("/bundles/x.raucb")
check("says where bundles actually live", "provisioning server" in r.text, r.text[:120])

print("== a missing file is not a route ==")
for path in ("/favicon-that-does-not-exist.ico", "/nested/thing.js", "/x.raucb"):
    r = client.get(path)
    check(f"404 for {path}", r.status_code == 404, f"got {r.status_code}")

print("== files that exist are still served ==")
r = client.get("/favicon.ico")
check("real asset served", r.status_code == 200 and r.text == "icon",
      f"got {r.status_code} {r.text[:40]!r}")

print("== client-side routes still reach the SPA ==")
# No extension and no server-owned prefix: these are the app's own routes and
# must keep getting index.html for the client router to handle. /images is the
# one to watch -- it is a real page, and only the trailing slash separates it
# from /images/, which belongs to the provisioning server.
for path in ("/", "/updates", "/images", "/fleet/host/1"):
    r = client.get(path)
    check(f"index.html for {path}",
          r.status_code == 200 and "<body>app</body>" in r.text,
          f"got {r.status_code}")

print("== the API's own 404 is unchanged ==")
r = client.get("/api/nope")
check("api 404", r.status_code == 404, f"got {r.status_code}")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
