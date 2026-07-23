"""Drives the builder / imager / provisioning server via the Docker socket."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from app.config import settings

HOST = settings.host_project_dir
HOST_OUT = settings.host_output_dir
PROJ = settings.project_dir       # path to the repo inside this container


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------- builds ---------------------------
def build_image_cmd(opts: dict) -> tuple[list[str], str, dict]:
    """Return (command, label, env) to build an A/B image.

    Secrets (login password, LUKS passphrase) travel via the environment —
    build-image.sh reads PASSWORD / LUKS_PASS — so they never appear on a
    command line visible in `ps` or in persisted job metadata.
    """
    distro = opts.get("distro", "debian")
    suite = opts.get("suite", "trixie")
    args = [
        "--distro", distro,
        "--suite", suite,
        "--hostname", opts.get("hostname", f"{distro}-ab"),
        "--username", opts.get("username", "debian"),
        "--image-size", ("auto" if opts.get("image_size") in ("auto", 0, "0", "", None)
                         else str(opts["image_size"])),
        "--root-size", str(opts.get("root_size", 3072)),
        "--compress", opts.get("compress", "zstd"),
    ]
    env = {"PASSWORD": opts.get("password", "debian")}
    if opts.get("packages"):
        args += ["--packages", opts["packages"]]
    if opts.get("ssh_key"):
        args += ["--ssh-authorized-key", opts["ssh_key"]]
    if opts.get("ssh_key_only"):
        args += ["--ssh-key-only"]
    if opts.get("encrypt"):
        args += ["--encrypt", "--unlock", opts.get("unlock", "keyfile")]
        env["LUKS_PASS"] = opts.get("luks_passphrase", "")
        if opts.get("unlock") == "tang" and opts.get("tang_url"):
            args += ["--tang-url", opts["tang_url"]]
    out_name = f"{distro}-{suite}-ab.img"
    # Build context paths are read by the docker CLI inside THIS container
    # (PROJECT_DIR); bind-mount sources are resolved by the daemon on the HOST.
    # `-e VAR` (no value) makes the docker CLI forward VAR from its own env.
    script = (
        "set -eo pipefail\n"
        f"docker build --platform=linux/amd64 -t debian-ab-builder {PROJ}/builder 2>&1 | tail -3\n"
        "echo '--- starting image build ---'\n"
        f"docker run --rm --privileged --platform=linux/amd64 -v {HOST_OUT}:/output "
        f"-e PASSWORD -e LUKS_PASS "
        f"debian-ab-builder {' '.join(_q(a) for a in args)} --output /output/{out_name}\n"
    )
    label = f"Build {distro}/{suite} image ({opts.get('hostname', f'{distro}-ab')})"
    return ["bash", "-c", script], label, env


def build_imager_cmd() -> tuple[list[str], str]:
    script = (
        "set -eo pipefail\n"
        f"docker build --platform=linux/amd64 -t debian-ab-imager {PROJ}/imager 2>&1 | tail -3\n"
        "echo '--- building imager ---'\n"
        f"docker run --rm --platform=linux/amd64 -v {HOST_OUT}:/output debian-ab-imager\n"
    )
    return ["bash", "-c", script], "Build netboot imager"


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


# --------------------------- images ---------------------------
def _sidecars(path: str) -> dict:
    """Read the builder's .sha256 / .json sidecars for an image, if present."""
    extra: dict = {}
    try:
        with open(path + ".sha256") as f:
            extra["sha256"] = f.read().split()[0]
    except (OSError, IndexError):
        pass
    try:
        with open(path + ".json") as f:
            extra["meta"] = json.load(f)
    except (OSError, ValueError):
        pass
    return extra


def list_images() -> tuple[list[dict], bool]:
    out = settings.output_dir
    items: list[dict] = []
    if os.path.isdir(out):
        for fn in sorted(os.listdir(out)):
            full = os.path.join(out, fn)
            if os.path.isfile(full) and re.search(r"\.img(\.zst|\.gz)?$", fn):
                st = os.stat(full)
                items.append({
                    "name": fn,
                    "size": st.st_size,
                    "created": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                    **_sidecars(full),
                })
    imager_dir = os.path.join(out, "imager")
    imager_ready = os.path.isfile(os.path.join(imager_dir, "vmlinuz")) and \
        os.path.isfile(os.path.join(imager_dir, "initramfs.img"))
    return items, imager_ready


def delete_image(name: str) -> None:
    if "/" in name or ".." in name:
        raise ValueError("invalid name")
    path = os.path.join(settings.output_dir, name)
    if not re.search(r"\.img(\.zst|\.gz)?$", name) or not os.path.isfile(path):
        raise FileNotFoundError(name)
    os.remove(path)
    for sidecar in (path + ".sha256", path + ".json"):
        if os.path.isfile(sidecar):
            os.remove(sidecar)


def disk_usage() -> dict:
    """Free space on the output volume and how much the artifacts occupy."""
    out = settings.output_dir
    used = 0
    for root, _dirs, files in os.walk(out):
        for fn in files:
            try:
                used += os.stat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    total, _used, free = shutil.disk_usage(out) if os.path.isdir(out) else (0, 0, 0)
    return {"artifacts": used, "free": free, "total": total}


# --------------------------- provisioning server ---------------------------
ENV_PATH = os.path.join(settings.project_dir, "server", ".env")
ENV_EXAMPLE = os.path.join(settings.project_dir, "server", ".env.example")
ENV_KEYS = [
    "SERVER_IP", "IMAGE_FILE", "ACTION", "MODE", "INTERFACE", "PROXY_SUBNET",
    "DHCP_RANGE_START", "DHCP_RANGE_END", "DHCP_NETMASK", "DHCP_ROUTER", "DHCP_DNS", "LEASE_TIME",
]


def read_env() -> dict:
    cfg: dict[str, str] = {}
    src = ENV_PATH if os.path.isfile(ENV_PATH) else ENV_EXAMPLE
    if os.path.isfile(src):
        for line in open(src):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return {k: cfg.get(k, "") for k in ENV_KEYS}


def write_env(cfg: dict) -> None:
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w") as f:
        f.write("# Managed by the web UI\n")
        for k in ENV_KEYS:
            if cfg.get(k):
                f.write(f"{k}={cfg[k]}\n")


def _compose(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOST_OUTPUT_DIR": HOST_OUT}
    return subprocess.run(
        ["docker", "compose", "-f", os.path.join(settings.project_dir, "server", "docker-compose.yml"), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


def server_status() -> dict:
    proc = _compose("ps", "--format", "json")
    running = "dnsmasq" in proc.stdout and "running" in proc.stdout.lower()
    return {"running": running, "detail": proc.stdout.strip() or proc.stderr.strip()}


def server_up() -> str:
    return (_compose("up", "-d", "--build").stderr or "started").strip()


def server_down() -> str:
    return (_compose("down").stderr or "stopped").strip()


def _docker_logs(container: str, tail: int) -> list[str]:
    try:
        proc = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    return (proc.stdout + proc.stderr).splitlines()


# nginx access-log line: IP ... "GET /path HTTP/1.1" status bytes
_NGINX_RE = re.compile(r'^(\S+) \S+ \S+ \[[^\]]*\] "GET (/\S*) HTTP/[^"]*" (\d{3}) (\d+)')


def server_clients() -> list[dict]:
    """Merge dnsmasq (PXE/DHCP/TFTP) and nginx (imager + image downloads) logs
    into per-machine imaging status."""
    seen: dict[str, dict] = {}
    for line in _docker_logs("debian-ab-dnsmasq", 400):
        mac = re.search(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", line)
        if not mac:
            continue
        m = mac.group(0)
        entry = seen.setdefault(m, {"mac": m, "ip": "", "event": "", "last": ""})
        ip = re.search(r"\b(\d{1,3}\.){3}\d{1,3}\b", line)
        if ip:
            entry["ip"] = ip.group(0)
        if "DHCPACK" in line:
            entry["event"] = "got boot info"
        elif "tftp" in line.lower() and "sent" in line.lower():
            entry["event"] = "downloading bootloader"
        elif "BOOTP" in line or "PXE" in line:
            entry["event"] = "PXE booting"
        entry["last"] = line[:19]

    # nginx completes a log line only when the transfer finishes, so a logged
    # 200 for the image file means the machine has fully downloaded (and
    # therefore written) the image.
    by_ip: dict[str, str] = {}
    for line in _docker_logs("debian-ab-http", 300):
        m = _NGINX_RE.match(line)
        if not m:
            continue
        ip, path, status, _nbytes = m.groups()
        if status not in ("200", "206"):
            continue
        if path.startswith("/imager/"):
            by_ip.setdefault(ip, "booting imager")
        elif path.startswith("/images/") and re.search(r"\.img(\.zst|\.gz)?$", path):
            by_ip[ip] = "imaged"
    for entry in seen.values():
        if entry["ip"] in by_ip:
            entry["event"] = by_ip.pop(entry["ip"])
    # HTTP clients dnsmasq never saw (e.g. proxyDHCP handled by the router).
    for ip, event in by_ip.items():
        seen[ip] = {"mac": "—", "ip": ip, "event": event, "last": ""}
    return list(seen.values())
