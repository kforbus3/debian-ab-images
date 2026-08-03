"""A durable record of which machine got which image, and whether it came back.

The imaging registry next door is deliberately in-memory and expiring: it
answers "what is happening right now". This answers a different question that
an in-memory view cannot -- "what is out there, and did it work" -- so it is
written to disk and never expires.

Two events make up the story of a deployment:

  imaged   the imager finished writing and verified the disk. It is sent before
           the machine reboots, so on its own it says nothing about whether the
           machine can actually boot what was just written to it.
  booted   the machine came up on the image and reported in. This is the part
           that closes the loop: without it, a machine that images perfectly and
           then fails to boot is indistinguishable from a success.

Stored as JSON Lines because the file is append-only in normal use, survives a
partial write at the end (one bad final line, not a corrupt database), and can
be read with tail and grep when something has gone wrong enough that the web UI
is not the tool you want.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from .config import settings

# Keep the on-disk log from growing without bound on a fleet that is reimaged
# often. Well beyond any realistic fleet, and only trimmed when exceeded.
MAX_EVENTS = 20000
TRIM_TO = 15000


def _path() -> str:
    return os.path.join(settings.output_dir, "deployments.jsonl")


class Deployments:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def record(self, event: str, ident: str, **fields: Any) -> dict[str, Any]:
        """Append one event. Never raises: losing a record must not fail a build
        or, worse, reject a machine's report and leave it looking stalled."""
        row = {"at": time.time(), "event": event, "id": ident}
        row.update({k: v for k, v in fields.items() if v not in (None, "")})
        try:
            with self._lock:
                os.makedirs(os.path.dirname(_path()), exist_ok=True)
                with open(_path(), "a") as f:
                    f.write(json.dumps(row) + "\n")
                self._trim_if_huge()
        except OSError:
            pass
        return row

    def _trim_if_huge(self) -> None:
        try:
            if os.path.getsize(_path()) < MAX_EVENTS * 200:
                return
            with open(_path()) as f:
                lines = f.readlines()
            if len(lines) <= MAX_EVENTS:
                return
            tmp = _path() + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(lines[-TRIM_TO:])
            os.replace(tmp, _path())
        except OSError:
            pass

    def events(self, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with open(_path()) as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue          # a torn final line is expected, not a fault
        return out

    def fleet(self) -> list[dict[str, Any]]:
        """One row per machine: what it was last given, and how that went."""
        by_id: dict[str, dict[str, Any]] = {}
        for e in self.events(limit=MAX_EVENTS):
            ident = e.get("id")
            if not ident:
                continue
            row = by_id.setdefault(ident, {"id": ident})
            if e["event"] == "imaged":
                # A re-image starts the story over: the previous boot result
                # describes an image this machine is no longer running.
                row.update({
                    "imaged_at": e.get("at"), "image": e.get("image", ""),
                    "disk": e.get("disk", ""), "address": e.get("address", ""),
                    "booted_at": None, "hostname": "", "slot": "", "version": "",
                })
            elif e["event"] == "booted":
                row.update({
                    "booted_at": e.get("at"),
                    "hostname": e.get("hostname", "") or row.get("hostname", ""),
                    "slot": e.get("slot", "") or row.get("slot", ""),
                    "version": e.get("version", "") or row.get("version", ""),
                    "address": e.get("address", "") or row.get("address", ""),
                })
        now = time.time()
        rows = list(by_id.values())
        for r in rows:
            imaged, booted = r.get("imaged_at"), r.get("booted_at")
            if booted:
                r["state"] = "running"
            elif imaged and now - imaged > 1800:
                # Half an hour is far longer than any machine needs to reboot
                # into what was just written to it. Silence past that is the
                # signal worth surfacing: it imaged, and it never came back.
                r["state"] = "never-booted"
            else:
                r["state"] = "imaged"
        rows.sort(key=lambda r: r.get("booted_at") or r.get("imaged_at") or 0, reverse=True)
        return rows


deployments = Deployments()
