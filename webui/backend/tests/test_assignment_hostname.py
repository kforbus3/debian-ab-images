#!/usr/bin/env python3
"""A machine's name is assigned per MAC, and must reach the machine intact.

An image cannot carry a hostname: every machine written from one image would
answer to the same thing, which is why the only way to name a machine used to be
logging into it afterwards and typing one. That is manual per-machine state on a
fleet whose whole premise is that machines are interchangeable -- and on this
project it was also how an operator walked into a boot-counter bug, because
"log in, set the hostname, reboot" is a sequence nobody should need to perform.

The name now travels: assignment -> per-machine iPXE script -> kernel command
line -> the imager -> /boot/ab-deploy.json -> machine-identity.service. This
covers the first two hops, which are the ones that can be checked without
booting anything.
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


def script_for(mac):
    path = os.path.join(orch.hosts_dir(), mac.replace(":", "-") + ".ipxe")
    with open(path) as f:
        return f.read()


# An image has to exist for an assignment to be accepted.
open(os.path.join(OUT, "test.img"), "w").close()
MAC_A = "aa:bb:cc:dd:ee:01"
MAC_B = "aa:bb:cc:dd:ee:02"

print("== a valid hostname reaches the kernel command line ==")
orch.write_assignments([{"mac": MAC_A, "image": "test.img", "hostname": "web01"}])
s = script_for(MAC_A)
check("imager.hostname=web01 is passed", "imager.hostname=web01" in s,
      [l for l in s.splitlines() if l.startswith("kernel")])
check("it survives a round trip through the store",
      orch.read_assignments()[0]["hostname"] == "web01")

print("== no hostname means the image's own name stands ==")
orch.write_assignments([{"mac": MAC_A, "image": "test.img"}])
s = script_for(MAC_A)
check("no imager.hostname argument at all", "imager.hostname" not in s)
# A machine imaged before this existed must not suddenly get an empty name.
check("nothing empty is passed", "imager.hostname=" not in s)

print("== an FQDN is allowed ==")
orch.write_assignments([{"mac": MAC_A, "image": "test.img", "hostname": "web01.corp.example"}])
check("dots are fine", "imager.hostname=web01.corp.example" in script_for(MAC_A))

print("== invalid hostnames are refused, not silently corrected ==")
# Sanitising would mean the name in the UI is not the name on the machine, and
# the first person to notice would be whoever could not resolve it. A space is
# the one that matters most: it would split into a second kernel parameter.
for bad in ("web 01", "-web01", "web01-", "web_01", "web01!", "a" * 64,
            "web..01", "café01"):
    try:
        orch.write_assignments([{"mac": MAC_A, "image": "test.img", "hostname": bad}])
        check(f"{bad!r} refused", False, "it was accepted")
    except ValueError:
        check(f"{bad!r} refused", True)

print("== a space can never reach the kernel command line ==")
# The consequence of the above, stated as the property that actually matters:
# everything after a space would be read by the kernel as another parameter.
orch.write_assignments([{"mac": MAC_A, "image": "test.img", "hostname": "web01"}])
kernel_line = [l for l in script_for(MAC_A).splitlines() if l.startswith("kernel")][0]
check("one imager.hostname token, no stray words",
      kernel_line.count("imager.hostname=") == 1 and
      " imager.hostname=web01 " in kernel_line, kernel_line)

print("== two machines cannot be given the same name ==")
try:
    orch.write_assignments([
        {"mac": MAC_A, "image": "test.img", "hostname": "web01"},
        {"mac": MAC_B, "image": "test.img", "hostname": "web01"},
    ])
    check("duplicate refused", False, "both were accepted")
except ValueError as exc:
    check("duplicate refused", True)
    check("the message names both machines",
          MAC_A in str(exc) and MAC_B in str(exc), str(exc))

print("== case differences are still the same name ==")
try:
    orch.write_assignments([
        {"mac": MAC_A, "image": "test.img", "hostname": "web01"},
        {"mac": MAC_B, "image": "test.img", "hostname": "WEB01"},
    ])
    check("WEB01 vs web01 refused", False, "both were accepted")
except ValueError:
    check("WEB01 vs web01 refused", True)

print("== two machines may both be left unnamed ==")
try:
    orch.write_assignments([
        {"mac": MAC_A, "image": "test.img"},
        {"mac": MAC_B, "image": "test.img"},
    ])
    check("blank is not a duplicate", True)
except ValueError as exc:
    check("blank is not a duplicate", False, str(exc))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
