"""Named users on disk, replacing the single shared admin password.

One shared password answers "may this request proceed" and nothing else: not
who asked, not what they should be allowed to do, and not how to lock one
person out without locking everyone out. These records answer all three.

Stored in `output/users.json` (mode 0600, atomic write-and-rename) because
output/ is where the app's other state already lives and is gitignored in
full. Passwords are bcrypt hashes -- the file leaking must cost an attacker a
password-cracking run, not a login.

Bootstrap keeps every existing deployment working unchanged: on first start
with no users file, the ADMIN_PASSWORD environment variable becomes the
`admin` user's password, hashed. Once the file exists the variable is ignored
-- deliberately not resynced on every boot, because a password changed in the
UI must not be silently reverted by an environment variable nobody remembers
setting.

A record may carry no password_hash at all, with `source` naming where the
principal is authenticated instead (an SSO provider, later). Such a user can
never log in with a password -- verify() refuses before bcrypt is consulted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time

import bcrypt

from app.config import settings

# Order is rank: everything an operator may do, a viewer may not necessarily,
# and admin may do everything. Comparisons go through role_at_least so the
# ordering lives in exactly one place.
ROLES = ("viewer", "operator", "admin")
_RANK = {r: i for i, r in enumerate(ROLES)}

USERNAME_RE = re.compile(r"^[a-z0-9._-]{1,32}$")

_FILE = "users.json"
_lock = threading.RLock()


class UserError(ValueError):
    """A refused change. The message says why and is safe to show the user."""


def role_at_least(role: str, minimum: str) -> bool:
    return _RANK.get(role, -1) >= _RANK.get(minimum, len(ROLES))


def _path() -> str:
    return os.path.join(settings.output_dir, _FILE)


def _write(users: dict[str, dict]) -> None:
    """Persist atomically at mode 0600: write a temp file, then rename over.

    A crash mid-write must not leave a truncated users file -- that would read
    as "no users exist", and the bootstrap would then recreate `admin` from an
    environment variable, silently reverting a changed password.
    """
    os.makedirs(settings.output_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=settings.output_dir, prefix=".users.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"users": list(users.values())}, f, indent=2)
        os.replace(tmp, _path())
    except BaseException:
        os.unlink(tmp)
        raise


def _read() -> dict[str, dict]:
    try:
        with open(_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        # Unreadable or corrupt. Treated as empty rather than missing: a file
        # that exists, however damaged, must never trigger the bootstrap --
        # see _write for why.
        return {}
    out: dict[str, dict] = {}
    for rec in data.get("users", []):
        if isinstance(rec, dict) and rec.get("username"):
            out[str(rec["username"])] = rec
    return out


def _bootstrap() -> None:
    """First start: turn ADMIN_PASSWORD into a real, hashed `admin` user."""
    password = settings.admin_password
    if not password:
        return
    _write({"admin": {
        "username": "admin",
        "password_hash": hash_password(password),
        "role": "admin",
        "disabled": False,
        "created": time.time(),
        "last_login": None,
        "source": "local",
    }})


def load() -> dict[str, dict]:
    """All users, keyed by username. Bootstraps on the very first call."""
    with _lock:
        if not os.path.exists(_path()):
            _bootstrap()
        return _read()


def get(username: str) -> dict | None:
    return load().get(str(username).strip().lower())


def public(rec: dict) -> dict:
    """A record as the API may show it: everything but the hash."""
    return {k: v for k, v in rec.items() if k != "password_hash"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify(username: str, password: str) -> dict | None:
    """The user record if the password is right, else None.

    None for every kind of failure -- unknown user, disabled, no password
    hash, wrong password -- so the caller cannot leak which one it was.
    """
    rec = get(username)
    if not rec or rec.get("disabled"):
        return None
    stored = rec.get("password_hash")
    if not stored:
        # A password-less record belongs to another identity source (SSO);
        # a typed password can never match it.
        return None
    try:
        if not bcrypt.checkpw(password.encode(), stored.encode()):
            return None
    except ValueError:
        return None
    with _lock:
        users = load()
        if rec["username"] in users:
            users[rec["username"]]["last_login"] = time.time()
            _write(users)
    return rec


def _validate_username(username: str) -> str:
    name = str(username).strip().lower()
    if not USERNAME_RE.match(name):
        raise UserError(
            "usernames are 1-32 characters of lowercase letters, digits, "
            "dot, underscore or hyphen")
    return name


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise UserError(f"role must be one of {', '.join(ROLES)}")
    return role


def _guard_last_admin(users: dict[str, dict], username: str, action: str) -> None:
    """Refuse to remove the last way into user management itself.

    Deleting, demoting or disabling the last enabled admin does not tighten
    anything -- it permanently locks user management, and the only repair is
    editing users.json by hand on the host.
    """
    target = users.get(username)
    if not target or target.get("role") != "admin" or target.get("disabled"):
        return
    others = [u for n, u in users.items()
              if n != username and u.get("role") == "admin" and not u.get("disabled")]
    if not others:
        raise UserError(
            f"cannot {action} {username}: it is the last enabled admin, and "
            "without one nobody can manage users, tokens or sessions again. "
            "Create another admin first.")


def create(username: str, password: str, role: str) -> dict:
    name = _validate_username(username)
    role = _validate_role(role)
    if len(password) < 8:
        raise UserError("the password must be at least 8 characters")
    with _lock:
        users = load()
        if name in users:
            raise UserError(f"user {name} already exists")
        rec = {
            "username": name,
            "password_hash": hash_password(password),
            "role": role,
            "disabled": False,
            "created": time.time(),
            "last_login": None,
            "source": "local",
        }
        users[name] = rec
        _write(users)
        return rec


def upsert_sso(username: str, role: str, source: str = "oidc") -> dict:
    """Create or refresh the record behind a successful SSO login.

    The role is rewritten on every login so a group change at the IdP
    propagates here at the next sign-in, not never. Two refusals are the
    point of this function existing at all:

    - A username that already belongs to a record this source does not own
      (a local user, or another provider's) is refused outright. Silently
      attaching an IdP identity to an existing local account is an account
      takeover -- whoever controls that username at the IdP would inherit the
      local user's rank -- so the collision is an error for an admin to
      resolve, never a merge.
    - A locally-disabled record stays refused. Disabling a user in this UI
      must end their access even while the IdP still vouches for them.
    """
    name = _validate_username(username)
    role = _validate_role(role)
    with _lock:
        users = load()
        rec = users.get(name)
        if rec is None:
            rec = {
                "username": name,
                # Deliberately no password_hash: verify() refuses such records
                # before bcrypt is consulted, so this user has exactly one way
                # in -- the IdP.
                "role": role,
                "disabled": False,
                "created": time.time(),
                "last_login": time.time(),
                "source": source,
            }
            users[name] = rec
            _write(users)
            return rec
        if rec.get("source") != source or rec.get("password_hash"):
            raise UserError(
                f"the username {name} already belongs to a "
                f"{rec.get('source') or 'local'} user here; refusing to attach "
                "the SSO identity to it. Rename one of the two.")
        if rec.get("disabled"):
            raise UserError(f"user {name} is disabled here")
        rec["role"] = role
        rec["last_login"] = time.time()
        _write(users)
        return rec


def set_password(username: str, password: str) -> dict:
    if len(password) < 8:
        raise UserError("the password must be at least 8 characters")
    with _lock:
        users = load()
        rec = users.get(_validate_username(username))
        if not rec:
            raise UserError(f"no such user: {username}")
        if rec.get("source") not in (None, "local"):
            # Giving an SSO record a password would quietly turn "the IdP is
            # the only way in" into two ways in, one of which the IdP cannot
            # revoke.
            raise UserError(
                f"{rec['username']} signs in via {rec['source']}; "
                "it has no password to reset")
        rec["password_hash"] = hash_password(password)
        _write(users)
        return rec


def set_role(username: str, role: str) -> dict:
    role = _validate_role(role)
    with _lock:
        users = load()
        name = _validate_username(username)
        rec = users.get(name)
        if not rec:
            raise UserError(f"no such user: {username}")
        if rec.get("role") == "admin" and role != "admin":
            _guard_last_admin(users, name, "demote")
        rec["role"] = role
        _write(users)
        return rec


def set_disabled(username: str, disabled: bool) -> dict:
    with _lock:
        users = load()
        name = _validate_username(username)
        rec = users.get(name)
        if not rec:
            raise UserError(f"no such user: {username}")
        if disabled:
            _guard_last_admin(users, name, "disable")
        rec["disabled"] = bool(disabled)
        _write(users)
        return rec


def delete(username: str) -> None:
    with _lock:
        users = load()
        name = _validate_username(username)
        if name not in users:
            raise UserError(f"no such user: {username}")
        _guard_last_admin(users, name, "delete")
        del users[name]
        _write(users)
