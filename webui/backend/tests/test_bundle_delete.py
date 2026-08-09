#!/usr/bin/env python3
"""Deleting a bundle must not leave the fleet fetching a file that is gone.

Bundles are not just files in a directory. `make-bundle.sh` ends by writing
`bundles/latest` with the name of what it just built, and `ab-update` with no
arguments -- the unattended path, the one a fleet actually uses -- fetches
`<server>/bundles/latest` and installs whatever it names. Directory listing is
off on the HTTP server, so that pointer is the only way an unattended machine
finds a bundle at all.

So a delete that removes the file and stops there does not fail locally. It
fails later, on every machine at once, as a 404 that ab-update reports as a
download failure or "is not a RAUC bundle" -- neither of which points at a file
someone deleted in the web UI days earlier.

These assert the pointer is always true: moved to the newest bundle that is
left, or removed when the last one goes.
"""
import json
import os
import sys
import tempfile

PROJ = tempfile.mkdtemp()
OUT = os.path.join(PROJ, "output")
BUNDLES = os.path.join(OUT, "bundles")
os.makedirs(BUNDLES, exist_ok=True)
os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
                  ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import orchestrator as orch  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def make_bundle(name, version, mtime):
    """Everything make-bundle.sh writes for one bundle, including the pointer."""
    path = os.path.join(BUNDLES, name)
    with open(path, "w") as f:
        f.write("squashfs-ish payload")
    with open(path + ".json", "w") as f:
        json.dump({"version": version, "compatible": "debian-ab"}, f)
    with open(path + ".sha256", "w") as f:
        f.write("0" * 64 + "\n")
    # mtime is what list_bundles sorts on, so the ordering has to be real.
    os.utime(path, (mtime, mtime))
    with open(os.path.join(BUNDLES, "latest"), "w") as f:
        f.write(name + "\n")


def latest():
    try:
        with open(os.path.join(BUNDLES, "latest")) as f:
            return f.read().strip()
    except OSError:
        return None


def clear():
    for f in os.listdir(BUNDLES):
        os.remove(os.path.join(BUNDLES, f))


print("== the listing says which bundle ab-update installs ==")
clear()
make_bundle("debian-ab-1.raucb", "1", 1_700_000_000)
make_bundle("debian-ab-2.raucb", "2", 1_700_001_000)
rows = {b["name"]: b for b in orch.list_bundles()}
check("two bundles listed", len(rows) == 2, list(rows))
check("the newest is flagged latest", rows["debian-ab-2.raucb"]["is_latest"] is True)
check("the older one is not", rows["debian-ab-1.raucb"]["is_latest"] is False)
check("sidecar metadata is merged in", rows["debian-ab-1.raucb"].get("version") == "1")

print("== deleting a non-latest bundle leaves the pointer alone ==")
res = orch.delete_bundle("debian-ab-1.raucb")
check("reports it was not latest", res["was_latest"] is False, res)
check("pointer unchanged", latest() == "debian-ab-2.raucb", latest())
check("the file is gone", not os.path.exists(os.path.join(BUNDLES, "debian-ab-1.raucb")))
check("its .json went too", not os.path.exists(os.path.join(BUNDLES, "debian-ab-1.raucb.json")))
check("its .sha256 went too", not os.path.exists(os.path.join(BUNDLES, "debian-ab-1.raucb.sha256")))
check("the other bundle is untouched", os.path.exists(os.path.join(BUNDLES, "debian-ab-2.raucb")))

print("== deleting the latest bundle moves the pointer to the next newest ==")
# This is the case that breaks a fleet. Three bundles, delete the newest, and
# the pointer must name the one that is now newest -- not the deleted file, and
# not the oldest.
clear()
make_bundle("debian-ab-1.raucb", "1", 1_700_000_000)
make_bundle("debian-ab-2.raucb", "2", 1_700_001_000)
make_bundle("debian-ab-3.raucb", "3", 1_700_002_000)
check("pointer starts at the newest", latest() == "debian-ab-3.raucb", latest())
res = orch.delete_bundle("debian-ab-3.raucb")
check("reports it was latest", res["was_latest"] is True, res)
check("reports the new latest", res["new_latest"] == "debian-ab-2.raucb", res)
check("pointer moved to the next newest", latest() == "debian-ab-2.raucb", latest())
check("the pointer names a file that exists",
      os.path.isfile(os.path.join(BUNDLES, latest() or "")))
check("no .tmp left behind", not os.path.exists(os.path.join(BUNDLES, "latest.tmp")))
rows = {b["name"]: b for b in orch.list_bundles()}
check("the listing agrees", rows["debian-ab-2.raucb"]["is_latest"] is True)

print("== deleting the last bundle removes the pointer rather than dangling it ==")
orch.delete_bundle("debian-ab-2.raucb")
res = orch.delete_bundle("debian-ab-1.raucb")
check("reports it was latest", res["was_latest"] is True, res)
check("no new latest to report", res["new_latest"] is None, res)
check("the pointer file is gone", latest() is None, latest())
check("the listing is empty", orch.list_bundles() == [])

print("== a bundle that is not there is a 404, not a crash ==")
clear()
try:
    orch.delete_bundle("nope.raucb")
    check("raises FileNotFoundError", False, "it returned instead")
except FileNotFoundError:
    check("raises FileNotFoundError", True)

print("== a name cannot escape the bundles directory ==")
# The name arrives from a URL path segment. A sentinel outside the directory
# proves the guard runs before the path is used, rather than merely existing.
sentinel = os.path.join(OUT, "keep-me.raucb")
with open(sentinel, "w") as f:
    f.write("not a bundle, and not yours to delete")
for bad in ("../keep-me.raucb", "sub/keep-me.raucb", "..", "/etc/passwd"):
    try:
        orch.delete_bundle(bad)
        check(f"{bad!r} refused", False, "it was accepted")
    except ValueError:
        check(f"{bad!r} refused", True)
    except FileNotFoundError:
        # Also a refusal, as long as nothing was removed.
        check(f"{bad!r} refused", True)
check("the file outside the directory survived", os.path.exists(sentinel))

print("== a name that is not a bundle is refused ==")
# The sidecars are deleted by name from the bundle path, so accepting
# "latest" here would delete the pointer and call it a bundle.
for bad in ("latest", "debian-ab-1.raucb.json", "debian-ab-1.raucb.sha256"):
    try:
        orch.delete_bundle(bad)
        check(f"{bad!r} refused", False, "it was accepted")
    except (ValueError, FileNotFoundError):
        check(f"{bad!r} refused", True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
