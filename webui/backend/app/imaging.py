"""Live state of machines currently being imaged.

The imager posts a line every time it changes phase. This keeps the most recent
one per machine and forgets it again once the machine is finished or has gone
quiet, so the page shows what is happening right now rather than a growing list
of everything that ever booted.

State is deliberately in memory. It describes machines that are imaging *at this
moment*; a restart of the web UI loses nothing that is not re-reported within
seconds by any machine still running, and anything that finished is not wanted
anyway. Persisting it would mean carrying stale rows across restarts and having
to expire them there too.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# How long a machine may go without reporting before it is treated as gone. The
# imager reports on phase changes rather than on a timer, and writing a large
# image to a slow disk is a long silence, so this is generous: the cost of
# holding a dead machine a little longer is a stale row, while expiring a live
# one mid-write makes the page lie about a machine that is working fine.
STALE_AFTER = 600.0

# How long a finished machine stays visible before it drops off. Long enough to
# see that it succeeded, short enough that the page returns to showing only
# active work.
KEEP_FINISHED = 90.0

_TERMINAL = {"done", "failed"}

# Phases in the order they occur, with the share of the run each represents.
# The imager reports a percentage for the long ones; the rest are derived so a
# machine that has only said "detected" does not sit at 0 and look stuck.
_PHASE_FLOOR = {
    "booted": 0,
    "detected": 5,
    "downloading": 10,
    "writing": 15,
    "verified": 90,
    "expanding": 95,
    "done": 100,
    "failed": 0,
}


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = {}

    def report(self, ident: str, phase: str, percent: str | int | None,
               detail: str = "", disk: str = "", url: str = "",
               address: str = "") -> dict[str, Any]:
        """Record one report from a machine."""
        now = time.time()
        try:
            pct = int(percent) if percent not in (None, "") else None
        except (TypeError, ValueError):
            pct = None
        floor = _PHASE_FLOOR.get(phase, 0)
        if pct is None or pct < floor:
            pct = floor

        with self._lock:
            row = self._rows.get(ident) or {"id": ident, "first_seen": now, "history": []}
            # Keep the phase sequence: an operator looking at a stuck machine
            # wants to know how far it got, not just where it stopped.
            if not row["history"] or row["history"][-1]["phase"] != phase:
                row["history"].append({"phase": phase, "at": now})
            row.update({
                "phase": phase,
                "percent": max(0, min(100, pct)),
                "detail": detail or row.get("detail", ""),
                "disk": disk or row.get("disk", ""),
                "image": url or row.get("image", ""),
                "address": address or row.get("address", ""),
                "last_seen": now,
                "finished_at": now if phase in _TERMINAL else None,
            })
            self._rows[ident] = row
            return dict(row)

    def active(self) -> list[dict[str, Any]]:
        """Machines worth showing, newest activity first, with the dead removed."""
        now = time.time()
        with self._lock:
            for ident, row in list(self._rows.items()):
                if row["phase"] in _TERMINAL:
                    # A finished machine lingers briefly, then goes.
                    if now - (row.get("finished_at") or row["last_seen"]) > KEEP_FINISHED:
                        del self._rows[ident]
                elif now - row["last_seen"] > STALE_AFTER:
                    # Stopped reporting without finishing: powered off, rebooted
                    # into the new image, or fell off the network. Either way it
                    # is not imaging now, which is what this page is about.
                    del self._rows[ident]
            rows = [dict(r) for r in self._rows.values()]

        for r in rows:
            r["age"] = round(now - r["first_seen"], 1)
            r["stale_for"] = round(now - r["last_seen"], 1)
            r["state"] = ("failed" if r["phase"] == "failed"
                          else "done" if r["phase"] == "done"
                          else "stalled" if r["stale_for"] > 120 else "active")
        rows.sort(key=lambda r: r["last_seen"], reverse=True)
        return rows

    def forget(self, ident: str) -> bool:
        with self._lock:
            return self._rows.pop(ident, None) is not None


registry = Registry()
