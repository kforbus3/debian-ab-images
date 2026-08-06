"""Optional secrets-manager integration for LUKS passphrases.

An encrypted image is built with a passphrase that, for every unlock method
except `passphrase`, is never typed again in normal operation: the machine
unlocks from its TPM, from Tang, or from a keyfile, and the passphrase exists
only as the recovery slot. That is precisely the kind of secret a person should
not be inventing in a form field and then keeping in a password note -- and the
one nobody can produce two years later when a TPM is cleared by a firmware
update and the machine stops at the initramfs prompt.

With a store configured, the web UI generates the passphrase itself, writes it
to the store keyed by image name, and only then starts the build. The order
matters and is not an implementation detail: an orphaned secret from a build
that failed is a housekeeping chore, while an encrypted image whose passphrase
was never persisted is unrecoverable.

The builder container never talks to the store. It receives the passphrase in
LUKS_PASS exactly as it does when one is typed, so nothing privileged gains
network access or a store credential.

Providers implement `SecretStore`. The one here speaks KV v2, which OpenBao and
HashiCorp Vault share, so a single client covers both.
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.config import settings

# Bits of entropy in a generated passphrase. token_urlsafe(32) is 256 bits in
# 43 URL-safe characters -- no shell metacharacters, so it survives the trip
# through the environment and into cryptsetup unmangled.
_PASSPHRASE_BYTES = 32

# Where the store's own configuration lives. output/ is bind-mounted, already
# holds the app's other state (deployments.jsonl), and is gitignored in full --
# which matters here because this file can hold a store token.
_CONFIG_NAME = ".secrets-store.json"

_HTTP_TIMEOUT = 15

_lock = threading.Lock()


class SecretStoreError(Exception):
    """Anything that stopped the store from answering. Message is user-facing."""


# --------------------------- configuration ---------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "openbao",          # openbao | vault -- same KV v2 API
    "address": "",                  # https://bao.example.lan:8200
    "mount": "secret",              # KV v2 mount point
    "path_prefix": "debian-ab-images",
    "namespace": "",                # Vault Enterprise / HCP namespace
    "auth_method": "token",         # token | approle
    "token": "",
    "role_id": "",
    "secret_id": "",
    "ca_cert": "",                  # PEM, for a private CA
    "tls_skip_verify": False,
}

# Never leaves the backend in an API response. The UI shows whether each is set,
# not what it is: a store credential read back out of the UI is a store
# credential one XSS away from being someone else's.
SECRET_FIELDS = ("token", "secret_id")


def config_path() -> str:
    return os.path.join(settings.output_dir, _CONFIG_NAME)


def read_config() -> dict[str, Any]:
    """The saved configuration, merged over the defaults."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path()) as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    # A token in the environment beats one on disk, so a deployment that injects
    # it (compose secret, systemd credential) never has to write it down here.
    for env in ("BAO_TOKEN", "VAULT_TOKEN"):
        if not cfg["token"] and os.environ.get(env):
            cfg["token"] = os.environ[env]
            break
    return cfg


def write_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into the saved configuration and persist it 0600.

    Secret fields are only overwritten when a non-empty value is supplied, so
    the UI can round-trip a redacted configuration without blanking the
    credential it was never shown.
    """
    with _lock:
        cfg = dict(DEFAULT_CONFIG)
        try:
            with open(config_path()) as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
        except (OSError, ValueError):
            pass
        for k, v in updates.items():
            if k not in DEFAULT_CONFIG:
                continue
            if k in SECRET_FIELDS and (v is None or v == ""):
                continue
            cfg[k] = v
        cfg["enabled"] = bool(cfg["enabled"])
        cfg["tls_skip_verify"] = bool(cfg["tls_skip_verify"])
        cfg["address"] = str(cfg["address"]).rstrip("/")
        cfg["path_prefix"] = str(cfg["path_prefix"]).strip("/")
        cfg["mount"] = str(cfg["mount"]).strip("/")

        os.makedirs(settings.output_dir, exist_ok=True)
        # Written to a temp file in the same directory and renamed, so a crash
        # mid-write cannot leave a truncated config that reads as "no store
        # configured" -- which would silently send the next build back to
        # requiring a typed passphrase.
        fd, tmp = tempfile.mkstemp(dir=settings.output_dir, prefix=".secrets-store.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, config_path())
        except BaseException:
            os.unlink(tmp)
            raise
        return cfg


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The configuration as the UI may see it: credentials replaced by a flag."""
    cfg = read_config() if cfg is None else cfg
    out = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
    for k in SECRET_FIELDS:
        out[f"{k}_set"] = bool(cfg.get(k))
    return out


# --------------------------- naming ---------------------------
def secret_name(image: str) -> str:
    """The store key for an image, independent of how it was compressed.

    The library holds `foo.img.zst` while an uncompressed build of the same
    thing is `foo.img`; both are the same image and must resolve to the same
    secret, or rebuilding with a different --compress would strand the
    passphrase under the old name.
    """
    name = os.path.basename(image.strip())
    for suffix in (".zst", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def generate_passphrase() -> str:
    return _secrets.token_urlsafe(_PASSPHRASE_BYTES)


# --------------------------- provider: KV v2 ---------------------------
class KVv2Store:
    """OpenBao / HashiCorp Vault KV v2.

    Deliberately built on urllib rather than a vendor SDK: the surface used here
    is four HTTP calls, and the alternative is a dependency in a container that
    holds the Docker socket.
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.address = str(cfg.get("address", "")).rstrip("/")
        self.mount = str(cfg.get("mount") or "secret").strip("/")
        self.prefix = str(cfg.get("path_prefix") or "").strip("/")
        self._token: str = ""
        self._token_expires: float = 0.0
        if not self.address:
            raise SecretStoreError("no store address configured")
        if not self.address.startswith(("http://", "https://")):
            raise SecretStoreError(f"store address must start with http:// or https:// (got '{self.address}')")

    # -- transport --
    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.address.startswith("https://"):
            return None
        if self.cfg.get("tls_skip_verify"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        ca = str(self.cfg.get("ca_cert") or "").strip()
        if ca:
            ctx = ssl.create_default_context()
            try:
                ctx.load_verify_locations(cadata=ca)
            except ssl.SSLError as exc:
                raise SecretStoreError(f"the CA certificate is not valid PEM: {exc}")
            return ctx
        return ssl.create_default_context()

    def _request(self, method: str, path: str, body: dict | None = None,
                 token: str | None = None) -> dict:
        url = f"{self.address}/v1/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            # X-Vault-Token is the header OpenBao kept for compatibility; it is
            # the only one both accept.
            req.add_header("X-Vault-Token", token)
        if self.cfg.get("namespace"):
            req.add_header("X-Vault-Namespace", str(self.cfg["namespace"]))
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT,
                                        context=self._ssl_context()) as resp:
                raw = resp.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise SecretStoreError(self._http_error(exc, path))
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise SecretStoreError(
                    f"TLS verification failed for {self.address}: {reason}. "
                    "Paste the CA certificate into the store settings, or use "
                    "an address whose certificate this container already trusts.")
            raise SecretStoreError(f"could not reach {self.address}: {reason}")
        except (TimeoutError, OSError) as exc:
            raise SecretStoreError(f"could not reach {self.address}: {exc}")
        except ValueError as exc:
            raise SecretStoreError(f"{self.address} returned a response that is not JSON: {exc}")

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError, path: str) -> str:
        detail = ""
        try:
            payload = json.loads(exc.read() or b"{}")
            errors = payload.get("errors") or []
            detail = "; ".join(str(e) for e in errors)
        except (ValueError, OSError):
            pass
        # These three are the ones an operator can actually act on, and the raw
        # status alone reads identically for all of them.
        if exc.code == 403:
            return (f"the store refused the credential (403){': ' + detail if detail else ''}. "
                    "Check the token or AppRole, and that its policy allows this path.")
        if exc.code == 404:
            return (f"not found at {path} (404). On KV v2 this is also what a wrong "
                    "mount point looks like -- check the mount, not just the path.")
        if exc.code in (503, 501):
            return f"the store is sealed or not initialised ({exc.code}){': ' + detail if detail else ''}"
        return f"store returned HTTP {exc.code}{': ' + detail if detail else ''}"

    # -- auth --
    def _auth_token(self) -> str:
        method = str(self.cfg.get("auth_method") or "token")
        if method == "token":
            token = str(self.cfg.get("token") or "")
            if not token:
                raise SecretStoreError("no store token configured")
            return token
        if method == "approle":
            now = time.monotonic()
            if self._token and now < self._token_expires:
                return self._token
            role_id = str(self.cfg.get("role_id") or "")
            secret_id = str(self.cfg.get("secret_id") or "")
            if not role_id or not secret_id:
                raise SecretStoreError("AppRole auth needs both a role ID and a secret ID")
            resp = self._request("POST", "auth/approle/login",
                                 {"role_id": role_id, "secret_id": secret_id})
            auth = resp.get("auth") or {}
            token = auth.get("client_token")
            if not token:
                raise SecretStoreError("AppRole login succeeded but returned no token")
            # Renew a minute early rather than discovering expiry mid-build.
            lease = int(auth.get("lease_duration") or 600)
            self._token = token
            self._token_expires = now + max(lease - 60, 30)
            return token
        raise SecretStoreError(f"unknown auth method '{method}'")

    # -- paths --
    def _data_path(self, name: str) -> str:
        parts = [self.mount, "data"] + ([self.prefix] if self.prefix else []) + [name]
        return "/".join(urllib.parse.quote(p, safe="") if p != "data" else p for p in parts)

    def _metadata_path(self) -> str:
        parts = [self.mount, "metadata"] + ([self.prefix] if self.prefix else [])
        return "/".join(urllib.parse.quote(p, safe="") if p != "metadata" else p for p in parts)

    def display_path(self, name: str) -> str:
        """Where a person would look for it in the store's own UI or CLI."""
        parts = [self.mount] + ([self.prefix] if self.prefix else []) + [name]
        return "/".join(parts)

    # -- operations --
    def health(self) -> dict:
        """Reachability, then credential, then the KV mount itself.

        Checked in that order because the three failures are indistinguishable
        from the UI otherwise -- an unreachable address, a token without the
        policy, and a mount that is KV v1 all end as "it didn't work".
        """
        info: dict[str, Any] = {}
        try:
            health = self._request("GET", "sys/health")
            info["sealed"] = health.get("sealed")
            info["version"] = health.get("version")
        except SecretStoreError:
            # Some deployments restrict sys/health; that is not fatal on its own,
            # and the calls below will report a genuine problem anyway.
            pass
        token = self._auth_token()
        try:
            lookup = self._request("GET", "auth/token/lookup-self", token=token)
            data = lookup.get("data") or {}
            info["token_policies"] = data.get("policies") or []
            if data.get("expire_time"):
                info["token_expires"] = data["expire_time"]
        except SecretStoreError:
            pass
        # The one check that must pass: KV v2 config on the configured mount.
        cfg_path = f"{urllib.parse.quote(self.mount, safe='')}/config"
        resp = self._request("GET", cfg_path, token=token)
        kv = resp.get("data") or {}
        if kv.get("max_versions") is None and kv.get("cas_required") is None:
            raise SecretStoreError(
                f"'{self.mount}' answered, but not like a KV v2 mount. "
                "A KV v1 mount cannot be used here -- version history is what makes "
                "an overwritten passphrase recoverable.")
        info["mount"] = self.mount
        info["kv_max_versions"] = kv.get("max_versions")
        return info

    def put(self, name: str, data: dict) -> str:
        self._request("POST", self._data_path(name), {"data": data}, token=self._auth_token())
        return self.display_path(name)

    def get(self, name: str) -> dict | None:
        try:
            resp = self._request("GET", self._data_path(name), token=self._auth_token())
        except SecretStoreError as exc:
            if "(404)" in str(exc):
                return None
            raise
        return (resp.get("data") or {}).get("data")

    def list(self) -> list[str]:
        path = self._metadata_path() + "?list=true"
        try:
            resp = self._request("GET", path, token=self._auth_token())
        except SecretStoreError as exc:
            if "(404)" in str(exc):
                return []
            raise
        return [k for k in (resp.get("data") or {}).get("keys", []) if not k.endswith("/")]


PROVIDERS = {"openbao": KVv2Store, "vault": KVv2Store}


def get_store(cfg: dict[str, Any] | None = None, *, require_enabled: bool = True):
    """The configured store, or raise with what to fix."""
    cfg = read_config() if cfg is None else cfg
    if require_enabled and not cfg.get("enabled"):
        raise SecretStoreError("no secrets manager is configured (Secrets Manager settings)")
    provider = str(cfg.get("provider") or "openbao")
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise SecretStoreError(f"unknown provider '{provider}'")
    return cls(cfg)


def is_configured() -> bool:
    cfg = read_config()
    return bool(cfg.get("enabled") and cfg.get("address"))


# --------------------------- the thing this exists for ---------------------------
def store_passphrase(image: str, passphrase: str, meta: dict[str, Any]) -> str:
    """Write an image's LUKS passphrase to the store. Returns the path written.

    Callers must do this *before* the build starts. See the module docstring:
    the failure that matters is an image whose passphrase was never persisted,
    not a secret left behind by a build that died.
    """
    store = get_store()
    payload = {
        "passphrase": passphrase,
        "image": secret_name(image),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Spelled out because whoever reads this is at a machine that will not
        # boot, and the store is unlikely to be the place they learn what a
        # recovery slot is.
        "note": ("LUKS2 recovery passphrase for this A/B image. Every machine imaged "
                 "from it accepts this passphrase on any encrypted partition. Rotating "
                 "it means re-imaging, or cryptsetup luksChangeKey on each machine."),
        **{k: str(v) for k, v in meta.items() if v not in (None, "")},
    }
    return store.put(secret_name(image), payload)


def fetch_passphrase(image: str) -> str | None:
    """An image's stored passphrase, or None if the store has no entry for it."""
    if not is_configured():
        return None
    data = get_store().get(secret_name(image))
    if not data:
        return None
    value = data.get("passphrase")
    return str(value) if value else None
