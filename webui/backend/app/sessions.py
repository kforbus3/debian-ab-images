"""Opaque, revocable sessions, replacing stateless session JWTs.

A JWT cannot be taken back: until it expires it is valid, and "log this
person out now" -- a departed employee, a stolen laptop -- has no
implementation. An opaque token looked up on every request costs one dict
lookup and buys exact, immediate revocation.

The browser holds `fls_<random>`; the server stores only its SHA-256 in
`output/.sessions.json` (mode 0600). A stolen copy of that file therefore
yields no working session -- hashes cannot be presented as tokens, and
SHA-256 of 256 random bits is not invertible. Sessions survive a backend
restart because the file does; nobody gets logged out by a redeploy.

Expiry is sliding: each use pushes the deadline 12 hours out, capped at 7
days from creation so a session cannot be kept alive forever. The refresh is
persisted at most once a minute -- the UI polls every few seconds, and
rewriting the file per poll would be almost all of its writes -- so a crash
costs at most a minute of refresh, never a session.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time

from app.config import settings

TOKEN_PREFIX = "fls_"
IDLE_SECONDS = 12 * 3600          # each use extends the session this far
MAX_SECONDS = 7 * 24 * 3600      # but never past this from creation
_PERSIST_INTERVAL = 60           # how often a sliding refresh reaches disk

_FILE = ".sessions.json"
_lock = threading.RLock()
_cache: dict[str, dict] | None = None    # sha256 -> record
_last_persist = 0.0


def _path() -> str:
    return os.path.join(settings.output_dir, _FILE)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _save(sessions: dict[str, dict]) -> None:
    global _last_persist
    os.makedirs(settings.output_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.output_dir, prefix=".sessions.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"sessions": sessions}, f, indent=2)
        os.replace(tmp, _path())
        _last_persist = time.monotonic()
    except BaseException:
        os.unlink(tmp)
        raise


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            with open(_path()) as f:
                data = json.load(f)
            _cache = {k: v for k, v in (data.get("sessions") or {}).items()
                      if isinstance(v, dict)}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _prune(sessions: dict[str, dict]) -> None:
    now = time.time()
    for k in [k for k, s in sessions.items() if s.get("expires", 0) <= now]:
        del sessions[k]


def create(username: str, ip: str = "") -> str:
    """Mint a session and return the one copy of its raw token."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        sessions = _load()
        _prune(sessions)
        sessions[_sha(raw)] = {
            "username": username,
            "created": now,
            "expires": now + IDLE_SECONDS,
            "ip": ip,
        }
        _save(sessions)
    return raw


def resolve(raw: str) -> dict | None:
    """The live session behind a raw token, refreshing its sliding expiry."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    now = time.time()
    with _lock:
        sessions = _load()
        rec = sessions.get(_sha(raw))
        if not rec or rec.get("expires", 0) <= now:
            return None
        if now - rec.get("created", now) >= MAX_SECONDS:
            return None
        rec["expires"] = min(now + IDLE_SECONDS, rec.get("created", now) + MAX_SECONDS)
        if time.monotonic() - _last_persist > _PERSIST_INTERVAL:
            _prune(sessions)
            _save(sessions)
        return dict(rec)


def revoke_raw(raw: str) -> bool:
    """Log out: the token presented is the one revoked."""
    with _lock:
        sessions = _load()
        if sessions.pop(_sha(raw), None) is None:
            return False
        _save(sessions)
        return True


def revoke_id(session_id: str) -> bool:
    """Admin revocation by the id list() shows."""
    with _lock:
        sessions = _load()
        for k in list(sessions):
            if k[:12] == session_id:
                del sessions[k]
                _save(sessions)
                return True
    return False


def revoke_user(username: str) -> int:
    """Every session a user holds -- for disable, delete and password reset."""
    with _lock:
        sessions = _load()
        doomed = [k for k, s in sessions.items() if s.get("username") == username]
        for k in doomed:
            del sessions[k]
        if doomed:
            _save(sessions)
        return len(doomed)


def list_sessions(current_raw: str = "") -> list[dict]:
    """Live sessions, ids only -- never the hashes, never a token."""
    current = _sha(current_raw) if current_raw else ""
    with _lock:
        sessions = _load()
        _prune(sessions)
        out = [{"id": k[:12], "current": k == current, **{
            f: s.get(f) for f in ("username", "created", "expires", "ip")}}
            for k, s in sessions.items()]
    out.sort(key=lambda s: s.get("created") or 0, reverse=True)
    return out
