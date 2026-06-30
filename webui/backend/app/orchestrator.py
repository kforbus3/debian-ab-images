"""Drives the builder / imager / provisioning server via the Docker socket."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone

from app.config import settings

HOST = settings.host_project_dir
HOST_OUT = settings.host_output_dir
PROJ = settings.project_dir       # path to the repo inside this container


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------- builds ---------------------------
def build_image_cmd(opts: dict) -> tuple[list[str], str]:
    """Return (command, label) to build an A/B image."""
    args = [
        "--hostname", opts.get("hostname", "debian-ab"),
        "--username", opts.get("username", "debian"),
        "--password", opts.get("password", "debian"),
        "--image-size", str(opts.get("image_size", 8)),
        "--root-size", str(opts.get("root_size", 3072)),
        "--suite", opts.get("suite", "trixie"),
        "--compress", opts.get("compress", "zstd"),
    ]
    if opts.get("packages"):
        args += ["--packages", opts["packages"]]
    out_name = f"debian-{opts.get('suite', 'trixie')}-ab.img"
    # Build context paths are read by the docker CLI inside THIS container
    # (PROJECT_DIR); bind-mount sources are resolved by the daemon on the HOST.
    script = (
        "set -eo pipefail\n"
        f"docker build --platform=linux/amd64 -t debian-ab-builder {PROJ}/builder 2>&1 | tail -3\n"
        "echo '--- starting image build ---'\n"
        f"docker run --rm --privileged --platform=linux/amd64 -v {HOST_OUT}:/output "
        f"debian-ab-builder {' '.join(_q(a) for a in args)} --output /output/{out_name}\n"
    )
    return ["bash", "-c", script], f"Build image ({opts.get('hostname', 'debian-ab')})"


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
def list_images() -> list[dict]:
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
                })
    imager_dir = os.path.join(out, "imager")
    imager_ready = os.path.isfile(os.path.join(imager_dir, "vmlinuz")) and \
        os.path.isfile(os.path.join(imager_dir, "initramfs.img"))
    return items, imager_ready  # type: ignore[return-value]


def delete_image(name: str) -> None:
    if "/" in name or ".." in name:
        raise ValueError("invalid name")
    path = os.path.join(settings.output_dir, name)
    if not re.search(r"\.img(\.zst|\.gz)?$", name) or not os.path.isfile(path):
        raise FileNotFoundError(name)
    os.remove(path)


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


def server_clients() -> list[dict]:
    """Parse recent dnsmasq logs to show machines that PXE-booted / are imaging."""
    try:
        proc = subprocess.run(
            ["docker", "logs", "--tail", "400", "debian-ab-dnsmasq"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    seen: dict[str, dict] = {}
    log = (proc.stdout + proc.stderr).splitlines()
    for line in log:
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
    return list(seen.values())
