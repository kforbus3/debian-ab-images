"""Long-lived API tokens for automation, stored hashed like sessions.

A CI pipeline cannot type a password into a login form, and lending it a
person's session means its access dies with their account and is
indistinguishable from them in the audit log. A token is its own principal:
named, role-limited, individually revocable, and visibly last-used -- which
is how a forgotten one is found.

The raw token (`flt_<random>`) is shown exactly once, at creation; only its
SHA-256 lands in `output/.api-tokens.json` (mode 0600). It is presented as
`Authorization: Bearer flt_...`, the same header the UI already uses, so a
curl in CI and the browser go through the same code path.

A token's role may not exceed its creator's -- a role ceiling, so no
credential can mint a credential more powerful than itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time

from app import users as userstore
from app.config import settings

TOKEN_PREFIX = "flt_"
_FILE = ".api-tokens.json"
_PERSIST_INTERVAL = 60           # how often last_used reaches disk
_lock = threading.RLock()
_cache: list[dict] | None = None
_last_persist = 0.0


class TokenError(ValueError):
    """A refused token operation. Message is safe to show the user."""


def _path() -> str:
    return os.path.join(settings.output_dir, _FILE)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _save(tokens: list[dict]) -> None:
    global _last_persist
    os.makedirs(settings.output_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.output_dir, prefix=".api-tokens.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"tokens": tokens}, f, indent=2)
        os.replace(tmp, _path())
        _last_persist = time.monotonic()
    except BaseException:
        os.unlink(tmp)
        raise


def _load() -> list[dict]:
    global _cache
    if _cache is None:
        try:
            with open(_path()) as f:
                data = json.load(f)
            _cache = [t for t in (data.get("tokens") or []) if isinstance(t, dict)]
        except (OSError, ValueError):
            _cache = []
    return _cache


def create(name: str, role: str, created_by: str, creator_role: str,
           expires_days: float | None = None) -> tuple[str, dict]:
    """Mint a token; returns (raw, record). The raw token exists only here."""
    name = str(name).strip()
    if not name or len(name) > 64:
        raise TokenError("a token needs a name (up to 64 characters)")
    if role not in userstore.ROLES:
        raise TokenError(f"role must be one of {', '.join(userstore.ROLES)}")
    if not userstore.role_at_least(creator_role, role):
        raise TokenError(
            f"a {creator_role} cannot create a {role} token -- a token's role "
            "may not exceed its creator's")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = time.time()
    rec = {
        "sha256": _sha(raw),
        "name": name,
        "role": role,
        "created": now,
        "created_by": created_by,
        "expires": now + float(expires_days) * 86400 if expires_days else None,
        "last_used": None,
    }
    with _lock:
        tokens = _load()
        if any(t.get("name") == name for t in tokens):
            raise TokenError(f"a token named {name} already exists")
        tokens.append(rec)
        _save(tokens)
    return raw, rec


def resolve(raw: str) -> dict | None:
    """The live token record behind a raw token, or None."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    sha = _sha(raw)
    now = time.time()
    with _lock:
        for rec in _load():
            if rec.get("sha256") != sha:
                continue
            if rec.get("expires") and rec["expires"] <= now:
                return None
            rec["last_used"] = now
            if time.monotonic() - _last_persist > _PERSIST_INTERVAL:
                _save(_load())
            return dict(rec)
    return None


def revoke(token_id: str) -> bool:
    with _lock:
        tokens = _load()
        for i, rec in enumerate(tokens):
            if rec.get("sha256", "")[:12] == token_id:
                del tokens[i]
                _save(tokens)
                return True
    return False


def list_tokens() -> list[dict]:
    """Every token, id + metadata only -- the hash stays out of API responses."""
    with _lock:
        out = [{"id": t.get("sha256", "")[:12],
                **{f: t.get(f) for f in ("name", "role", "created", "created_by",
                                         "expires", "last_used")}}
               for t in _load()]
    out.sort(key=lambda t: t.get("created") or 0, reverse=True)
    return out
