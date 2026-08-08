#!/usr/bin/env python3
"""A build must never replace an existing image by accident.

The output name was distro-suite-arch and nothing else, so building a second
Debian 13 amd64 image silently replaced the first. Three things went with it,
in rising order of how much it costs:

  the image file, so a bundle can no longer be built from what a deployed
  machine was actually made from;

  its sidecars, so the record of what that machine is goes too;

  and the LUKS passphrase in the secrets manager, which is filed *under the
  image name* -- so an unrelated later build destroys the recovery key of a
  machine already in the field, and nothing reveals it until someone needs it.

None of that announced itself. These assert that it cannot happen again.
"""
import os
import sys
import tempfile

PROJ = tempfile.mkdtemp()
OUT = os.path.join(PROJ, "output")
os.makedirs(OUT, exist_ok=True)
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


def touch(fn):
    open(os.path.join(OUT, fn), "w").close()


def clear():
    # Files only: importing the app creates output/jobs, and the point here is
    # the image library rather than everything in the directory.
    for f in os.listdir(OUT):
        p = os.path.join(OUT, f)
        if os.path.isfile(p):
            os.remove(p)


base = {"distro": "debian", "suite": "trixie", "arch": "amd64"}

print("== the first build gets the obvious name ==")
clear()
o = dict(base)
check("debian-trixie-amd64-ab.img", orch.resolve_output_name(o) == "debian-trixie-amd64-ab.img",
      o.get("name"))

print("== a second build of the same kind does not overwrite it ==")
touch("debian-trixie-amd64-ab.img")
o = dict(base)
n2 = orch.resolve_output_name(o)
check("gets a free name", n2 == "debian-trixie-amd64-ab-2.img", n2)
check("the first image is still there", os.path.exists(os.path.join(OUT, "debian-trixie-amd64-ab.img")))

print("== and a third ==")
touch("debian-trixie-amd64-ab-2.img")
check("-3", orch.resolve_output_name(dict(base)) == "debian-trixie-amd64-ab-3.img")

print("== compression does not create a second identity ==")
# The library lists .img, .img.zst and .img.gz alike, so a zstd build must not
# land beside an uncompressed image of the same name and be told apart by
# extension alone.
clear()
touch("debian-trixie-amd64-ab.img.zst")
check("a .zst counts as taken",
      orch.resolve_output_name(dict(base)) == "debian-trixie-amd64-ab-2.img")
clear()
touch("debian-trixie-amd64-ab.img.gz")
check("a .gz counts as taken",
      orch.resolve_output_name(dict(base)) == "debian-trixie-amd64-ab-2.img")

print("== a name that is asked for is honoured ==")
clear()
o = {**base, "name": "web-fleet-v3"}
check("web-fleet-v3.img", orch.resolve_output_name(o) == "web-fleet-v3.img")
o = {**base, "name": "web-fleet-v3.img"}
check("the .img suffix is not doubled", orch.resolve_output_name(o) == "web-fleet-v3.img")

print("== a name that is taken is refused, not silently changed ==")
touch("web-fleet-v3.img")
try:
    orch.resolve_output_name({**base, "name": "web-fleet-v3"})
    check("refuses", False, "it returned a name instead of raising")
except orch.NameInUse as exc:
    check("refuses", True)
    check("suggests a free one", exc.suggestion == "web-fleet-v3-2.img", exc.suggestion)

print("== unless replacing is asked for explicitly ==")
check("replace=True is honoured",
      orch.resolve_output_name({**base, "name": "web-fleet-v3", "replace": True})
      == "web-fleet-v3.img")

print("== a name cannot escape the output directory ==")
clear()
# basename first, then sanitise what is left: a traversal reduces to its last
# segment rather than being mangled into a name that still reads like a path.
for bad, want in (("../../etc/passwd", "passwd.img"),
                  ("/absolute/thing", "thing.img"),
                  ("sub/dir/name", "name.img"),
                  ("....", "image.img"),
                  ("", "debian-trixie-amd64-ab.img")):
    got = orch.resolve_output_name({**base, "name": bad})
    check(f"{bad!r} -> {got}", got == want, f"expected {want}")
    clear()

print("== the passphrase is filed under the name the build will use ==")
# This is the one that costs a machine. Both the secrets filing and the build
# command read opts["name"], which resolve_output_name has already settled --
# if they recomputed it independently they could disagree, and the passphrase
# would be filed against an image that never gets written.
clear()
touch("debian-trixie-amd64-ab.img")
o = dict(base)
resolved = orch.resolve_output_name(o)
check("opts carries the resolved name", o.get("name") == resolved, o.get("name"))
check("image_output_name agrees", orch.image_output_name(o) == resolved,
      orch.image_output_name(o))
cmd, _label, _env = orch.build_image_cmd(o)
check("the build command writes that file", f"--output /output/{resolved}" in " ".join(cmd),
      " ".join(cmd)[-120:])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
