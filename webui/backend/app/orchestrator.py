"""Drives the builder / imager / provisioning server via the Docker socket."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone

from app.config import settings
from app.jobs import JOB_TOKEN, container_name

PROJ = settings.project_dir       # path to the repo inside this container


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------- host path discovery ---------------------------
# The builder/imager/server containers are started through the Docker socket, so
# their bind-mount sources must be paths the DAEMON can see (host paths), while
# `docker build` contexts are read by the CLI in here (container paths). Getting
# the host path from a hand-set env var is the single easiest thing to get wrong
# — a stale value silently mounts an empty directory and every build dies with
# `unable to prepare context: path "/project/builder" not found`. So ask the
# daemon what it actually mounted at PROJECT_DIR instead.

# The daemon bind-mounts /etc/hosts, /etc/hostname and /etc/resolv.conf out of
# /var/lib/docker/containers/<id>/, so mountinfo identifies us even when the
# container hostname has been overridden.
_SELF_ID_RE = re.compile(r"/containers/([0-9a-f]{12,})/")


def _self_container_id() -> str:
    try:
        with open("/proc/self/mountinfo") as f:
            m = _SELF_ID_RE.search(f.read())
            if m:
                return m.group(1)
    except OSError:
        pass
    return socket.gethostname().strip()


_detected: str = ""


def host_project_dir() -> str:
    """Host-side path of the repo: explicit override, else our own bind mount.

    Only successful lookups are cached — a transient Docker socket hiccup at
    startup shouldn't pin an empty answer for the life of the process.
    """
    global _detected
    if settings.host_project_dir:
        return settings.host_project_dir.rstrip("/")
    if _detected:
        return _detected
    try:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", _self_container_id()],
            capture_output=True, text=True, timeout=15,
        )
        for mount in json.loads(proc.stdout or "[]"):
            if mount.get("Destination") == PROJ and mount.get("Source"):
                _detected = str(mount["Source"]).rstrip("/")
                return _detected
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ""


def host_output_dir() -> str:
    return f"{host_project_dir()}/output"


def _self_image() -> str:
    """This container's image, reused for throwaway host-namespace helpers."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", _self_container_id()],
            capture_output=True, text=True, timeout=15,
        )
        return proc.stdout.strip() or "debian-ab-webui"
    except (OSError, subprocess.SubprocessError):
        return "debian-ab-webui"


# --------------------------- host interfaces ---------------------------
# Docker's own bridges and virtual links: no physical machine ever PXE-boots on
# one, so listing them only clutters the choice.
_VIRTUAL_IF_RE = re.compile(r"^(docker\d+|br-[0-9a-f]{12}|veth|virbr\d+-nic|tun\d+|tap\d+)")

# `ip -d link` reports linkinfo.info_kind for every synthetic device and omits it
# for real NICs, which is a far better filter than guessing from names. A kernel
# leaves a pile of always-present tunnel stubs lying around (gre0, sit0, tunl0,
# erspan0, ip6tnl0, …) and none of them can carry a PXE client. These kinds are
# the ones that legitimately can, alongside genuine hardware.
_USABLE_IF_KINDS = {"vlan", "bond", "bridge", "macvlan", "team"}


def list_interfaces() -> list[dict]:
    """The host's IPv4 interfaces, so the UI can offer them instead of asking
    the operator to know their own topology.

    We have the Docker socket but not the host's network namespace, so read them
    through a throwaway container that does have it. `default` marks the NIC
    carrying the default route — that is the main LAN, and the one you do *not*
    want a standalone DHCP server on.
    """
    def _run(*cmd: str) -> str:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "host", _self_image(), *cmd],
            capture_output=True, text=True, timeout=60,
        )
        return proc.stdout
    try:
        # `ip -4 addr` omits an interface entirely when it has no IPv4 address,
        # so links are enumerated separately. The provisioning NIC is exactly
        # the one likely to have no address — it faces an isolated segment where
        # this server is supposed to BE the DHCP server — and leaving it out of
        # the list made it impossible to choose.
        links = json.loads(_run("ip", "-d", "-j", "link") or "[]")
        addrs = json.loads(_run("ip", "-j", "-4", "addr") or "[]")
        routes = json.loads(_run("ip", "-j", "-4", "route") or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    default_if = next((r.get("dev") for r in routes if r.get("dst") == "default"), None)

    by_name: dict[str, dict] = {}
    for entry in addrs:
        for addr in entry.get("addr_info", []):
            if addr.get("family") != "inet" or not addr.get("local"):
                continue
            # /31 and /32 can't host a subnet of PXE clients.
            if addr.get("prefixlen", 32) >= 31:
                continue
            by_name.setdefault(entry.get("ifname", ""), addr)

    out: list[dict] = []
    for link in links:
        name = link.get("ifname")
        if not name or name == "lo" or _VIRTUAL_IF_RE.match(name):
            continue
        if link.get("link_type") != "ether":
            continue
        kind = (link.get("linkinfo") or {}).get("info_kind")
        if kind and kind not in _USABLE_IF_KINDS:
            continue
        addr = by_name.get(name)
        item = {
            "name": name,
            "ip": "",
            "prefixlen": 0,
            "network": "",
            "netmask": "",
            "mac": link.get("address", ""),
            "up": link.get("operstate") not in ("DOWN",),
            "carrier": link.get("operstate") == "UP",
            "default": name == default_if,
        }
        if addr:
            try:
                net = ipaddress.ip_network(f"{addr['local']}/{addr['prefixlen']}", strict=False)
                item.update(ip=addr["local"], prefixlen=addr["prefixlen"],
                            network=str(net.network_address), netmask=str(net.netmask))
            except ValueError:
                pass
        out.append(item)
    # Addressless NICs first: on a turnkey setup that is the provisioning one.
    return sorted(out, key=lambda i: (bool(i["ip"]), i["default"], i["name"]))


# Candidate subnets for an unconfigured provisioning NIC, in preference order.
# Deliberately uncommon so they are unlikely to collide with the LAN the server
# is already on.
_CANDIDATE_NETS = ["192.168.50.0/24", "10.42.0.0/24", "172.30.0.0/24", "192.168.150.0/24"]


def suggest_provisioning_net(interfaces: list[dict] | None = None) -> dict:
    """A static address for a NIC that has none, avoiding subnets already in use."""
    interfaces = interfaces if interfaces is not None else list_interfaces()
    taken = []
    for i in interfaces:
        if i.get("ip") and i.get("prefixlen"):
            try:
                taken.append(ipaddress.ip_network(f"{i['ip']}/{i['prefixlen']}", strict=False))
            except ValueError:
                pass
    for cand in _CANDIDATE_NETS:
        net = ipaddress.ip_network(cand)
        if any(net.overlaps(t) for t in taken):
            continue
        return {
            "SERVER_IP": str(net.network_address + 1),
            "prefixlen": net.prefixlen,
            "DHCP_NETMASK": str(net.netmask),
            "PROXY_SUBNET": str(net.network_address),
            "DHCP_RANGE_START": str(net.network_address + 100),
            "DHCP_RANGE_END": str(net.network_address + 200),
        }
    return {}


def suggest_dhcp_range(ip: str, prefixlen: int) -> dict:
    """A lease range inside an interface's own subnet, avoiding .0/.1 and the
    broadcast address, so standalone DHCP needs no manual arithmetic."""
    try:
        net = ipaddress.ip_network(f"{ip}/{prefixlen}", strict=False)
    except ValueError:
        return {}
    size = net.num_addresses
    if size < 8:
        return {}
    lo, hi = min(100, size // 4), min(200, size - 2)
    if lo >= hi:
        lo, hi = 2, size - 2
    return {
        "DHCP_RANGE_START": str(net.network_address + lo),
        "DHCP_RANGE_END": str(net.network_address + hi),
        "DHCP_NETMASK": str(net.netmask),
        "PROXY_SUBNET": str(net.network_address),
    }


def preflight() -> list[str]:
    """Problems that would make builds fail, in plain language. Empty = ready."""
    problems: list[str] = []
    if not os.path.isfile(os.path.join(PROJ, "builder", "Dockerfile")):
        problems.append(
            f"The repository is not mounted at {PROJ} in the web UI container "
            f"({PROJ}/builder/Dockerfile is missing). Check the `:{PROJ}` volume in "
            "webui/docker-compose.yml — if HOST_PROJECT_DIR is set in webui/.env it "
            "must be the absolute host path of this checkout. Unset it to have the "
            "path detected automatically, then re-run `docker compose up -d`."
        )
    if not host_project_dir():
        problems.append(
            "Could not determine the repository's path on the Docker host, so the "
            "builder container would get an unusable output mount. Set "
            "HOST_PROJECT_DIR in webui/.env to this checkout's absolute host path."
        )
    if not os.access("/var/run/docker.sock", os.W_OK):
        problems.append(
            "The Docker socket is not available at /var/run/docker.sock. The web UI "
            "needs it to run the builder; check the volume in webui/docker-compose.yml."
        )
    return problems


# --------------------------- builds ---------------------------
def build_image_cmd(opts: dict) -> tuple[list[str], str, dict]:
    """Return (command, label, env) to build an A/B image.

    Secrets (login password, LUKS passphrase) travel via the environment —
    build-image.sh reads PASSWORD / LUKS_PASS — so they never appear on a
    command line visible in `ps` or in persisted job metadata.
    """
    distro = opts.get("distro", "debian")
    suite = opts.get("suite", "trixie")
    # The builder container runs as the architecture it is building, so
    # debootstrap and every chroot step execute natively rather than under
    # emulation.
    arch = opts.get("arch", "amd64")
    platform = f"linux/{arch}"
    args = [
        "--distro", distro,
        "--suite", suite,
        "--hostname", opts.get("hostname", f"{distro}-ab"),
        "--username", opts.get("username", "debian"),
        "--image-size", ("auto" if opts.get("image_size") in ("auto", 0, "0", "", None)
                         else str(opts["image_size"])),
        "--root-size", str(opts.get("root_size", 3072)),
        "--compress", opts.get("compress", "zstd"),
        "--arch", arch,
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
    # The image name carries the architecture, so an amd64 and an arm64 build of
    # the same suite do not overwrite one another in /output.
    out_name = f"{distro}-{suite}-{arch}-ab.img"
    # `-e VAR` (no value) makes the docker CLI forward VAR from its own env.
    script = (
        _docker_build("builder", f"debian-ab-builder:{arch}", platform)
        + "echo '--- starting image build ---'\n"
        + f"docker run --rm --name {container_name(JOB_TOKEN)} "
        + f"--privileged --platform={platform} -v {_q(host_output_dir())}:/output "
        + "-e PASSWORD -e LUKS_PASS "
        + f"debian-ab-builder:{arch} {' '.join(_q(a) for a in args)} --output /output/{out_name}\n"
    )
    label = f"Build {distro}/{suite} {arch} image ({opts.get('hostname', f'{distro}-ab')})"
    return ["bash", "-c", script], label, env


def build_imager_cmd() -> tuple[list[str], str]:
    script = (
        _docker_build("imager", "debian-ab-imager")
        + "echo '--- building imager ---'\n"
        + f"docker run --rm --name {container_name(JOB_TOKEN)} "
        + f"--platform=linux/amd64 -v {_q(host_output_dir())}:/output debian-ab-imager\n"
    )
    return ["bash", "-c", script], "Build netboot imager"


def _docker_build(subdir: str, tag: str, platform: str = "linux/amd64") -> str:
    """Shell prelude that builds one of the repo's images.

    The build CONTEXT is a path inside this container (the docker CLI tars it up
    here), unlike the run-time bind mounts above, which the daemon resolves on
    the host. `--progress=plain` keeps BuildKit's output line-oriented so it
    streams to the browser as it happens rather than arriving in one lump at the
    end — this step can take minutes and a silent log looks like a hang.
    """
    return (
        "set -eo pipefail\n"
        f"echo '--- building {subdir} image ---'\n"
        f"docker build --progress=plain --platform={platform} -t {tag} {_q(PROJ + '/' + subdir)}\n"
    )


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
    "SERVER_IP", "SERVER_PREFIXLEN", "IMAGE_FILE", "ACTION", "MODE", "INTERFACE", "PROXY_SUBNET",
    "DHCP_RANGE_START", "DHCP_RANGE_END", "DHCP_NETMASK", "DHCP_ROUTER", "DHCP_DNS", "LEASE_TIME",
    "UNASSIGNED", "RETRY_SECONDS",
]


def provisioning_preflight(cfg: dict | None = None) -> list[str]:
    """What still stands between the current config and a working PXE boot.

    Checked before the server starts so a misconfiguration surfaces in the UI
    rather than as a machine that PXE-boots into nothing.
    """
    cfg = cfg if cfg is not None else read_env()
    problems: list[str] = []

    _images, imager_ready = list_images()
    if not imager_ready:
        problems.append(
            "The netboot imager has not been built. Build it on the Build Image "
            "page — machines download its kernel and initramfs to boot."
        )
    image = cfg.get("IMAGE_FILE", "")
    if not image:
        problems.append("No image selected to deploy.")
    elif not os.path.isfile(os.path.join(settings.output_dir, image)):
        problems.append(f"The selected image '{image}' is not in the image library.")

    if not cfg.get("INTERFACE"):
        problems.append(
            "No provisioning interface selected. One is required: it confines "
            "DHCP and TFTP to that network so this server cannot answer, or "
            "interfere with, anything on your other networks."
        )
    if not cfg.get("SERVER_IP"):
        problems.append("No server IP — pick a provisioning interface to fill it in.")

    if cfg.get("MODE", "dhcp") == "dhcp":
        if not (cfg.get("DHCP_RANGE_START") and cfg.get("DHCP_RANGE_END")):
            problems.append("Standalone DHCP needs a lease range.")
    elif not cfg.get("PROXY_SUBNET"):
        problems.append("Proxy mode needs the subnet of the imaging network.")
    return problems


def read_env() -> dict:
    """Saved config, or turnkey defaults when nothing has been saved yet.

    Deliberately not seeded from .env.example: its illustrative 192.168.1.x
    values look like real settings in the UI, and a wrong-but-plausible server
    IP is worse than an empty field the operator is prompted to fill.
    """
    cfg: dict[str, str] = {}
    if os.path.isfile(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    else:
        cfg = default_env()
    return {k: cfg.get(k, "") for k in ENV_KEYS}


def write_env(cfg: dict) -> None:
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w") as f:
        f.write("# Managed by the web UI\n")
        for k in ENV_KEYS:
            if cfg.get(k):
                f.write(f"{k}={cfg[k]}\n")
    # Per-machine scripts embed the server IP and the default action, so they
    # go stale the moment either changes. Rewrite them from the stored
    # assignments rather than leaving machines pointed at the old address.
    try:
        existing = read_assignments()
        if existing:
            write_assignments(existing)
    except (OSError, ValueError):
        pass


# --------------------------- per-machine targeting ---------------------------
# A machine's iPXE dispatcher asks for /hosts/<mac>.ipxe before falling back to
# the default image (see server/http/boot.ipxe.tmpl). Assignments are stored as
# JSON — the source of truth — and the .ipxe files are generated from it, so a
# change of server IP or image regenerates them all consistently.
_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


def hosts_dir() -> str:
    return os.path.join(settings.output_dir, "hosts")


def _assign_path() -> str:
    return os.path.join(hosts_dir(), "assignments.json")


def normalize_mac(mac: str) -> str:
    """Canonical colon-separated lowercase, or '' if not a MAC."""
    mac = (mac or "").strip().lower().replace("-", ":")
    return mac if _MAC_RE.match(mac) else ""


def _mac_filename(mac: str) -> str:
    """iPXE's ${mac:hexhyp} form — hyphens, lowercase."""
    return mac.replace(":", "-") + ".ipxe"


def read_assignments() -> list[dict]:
    try:
        with open(_assign_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for a in data if isinstance(data, list) else []:
        mac = normalize_mac(a.get("mac", ""))
        if mac and a.get("image"):
            out.append({"mac": mac, "image": a["image"],
                        "action": a.get("action") or "", "name": a.get("name", "")})
    return sorted(out, key=lambda a: a["mac"])


def write_assignments(items: list[dict]) -> list[dict]:
    """Validate, persist, and regenerate the per-machine iPXE scripts."""
    cfg = read_env()
    known = {i["name"] for i in list_images()[0]}
    clean: list[dict] = []
    seen: set[str] = set()
    for a in items or []:
        mac = normalize_mac(a.get("mac", ""))
        if not mac:
            raise ValueError(f"'{a.get('mac', '')}' is not a MAC address")
        if mac in seen:
            raise ValueError(f"{mac} is assigned twice")
        image = (a.get("image") or "").strip()
        if not image:
            raise ValueError(f"{mac} has no image selected")
        if image not in known:
            raise ValueError(f"{mac}: image '{image}' is not in the library")
        seen.add(mac)
        clean.append({"mac": mac, "image": image,
                      "action": (a.get("action") or "").strip(),
                      "name": (a.get("name") or "").strip()})

    os.makedirs(hosts_dir(), exist_ok=True)
    tmp = _assign_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(clean, f, indent=2)
    os.replace(tmp, _assign_path())

    # Regenerate scripts, dropping any that no longer have an assignment.
    wanted = {_mac_filename(a["mac"]) for a in clean}
    for fn in os.listdir(hosts_dir()):
        if fn.endswith(".ipxe") and fn not in wanted:
            os.remove(os.path.join(hosts_dir(), fn))
    for a in clean:
        _write_host_script(a, cfg)
    return clean


def _write_host_script(a: dict, cfg: dict) -> None:
    ip = cfg.get("SERVER_IP", "")
    action = a["action"] or cfg.get("ACTION") or "reboot"
    label = a["name"] or a["mac"]
    script = f"""#!ipxe
# Generated by the web UI for {a['mac']} — do not edit; edit the assignment.
echo
echo ====================================================
echo   A/B Network Imager
echo   Machine: {label}
echo   Image  : {a['image']}
echo   Action : {action} after imaging
echo ====================================================
echo Booting the imager... this machine's disk will be re-imaged.
echo

kernel http://{ip}/imager/vmlinuz imager.url=http://{ip}/images/{a['image']} imager.action={action} imager.compress=auto console=tty0 console=ttyS0,115200
initrd http://{ip}/imager/initramfs.img
boot
"""
    with open(os.path.join(hosts_dir(), _mac_filename(a["mac"])), "w") as f:
        f.write(script)


def _compose(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOST_OUTPUT_DIR": host_output_dir()}
    return subprocess.run(
        ["docker", "compose", "-f", os.path.join(settings.project_dir, "server", "docker-compose.yml"), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


def server_status() -> dict:
    # compose refuses to run at all without the env_file, and its raw complaint
    # ("stat /project/server/.env: no such file...") reads like a fault rather
    # than the ordinary "you haven't configured this yet" that it is.
    if not os.path.isfile(ENV_PATH):
        return {"running": False,
                "detail": "Not configured yet — save the settings below to create server/.env."}
    proc = _compose("ps", "--format", "json")
    running = "dnsmasq" in proc.stdout and "running" in proc.stdout.lower()
    return {"running": running, "detail": proc.stdout.strip() or proc.stderr.strip()}


def server_up() -> str:
    return (_compose("up", "-d", "--build").stderr or "started").strip()


def default_env() -> dict:
    """Turnkey starting point: standalone DHCP on its own segment.

    Proxy mode depends on the LAN's existing DHCP server; standalone owns the
    provisioning network outright, which is what makes it self-contained.
    """
    return {"MODE": "dhcp", "ACTION": "reboot", "LEASE_TIME": "1h",
            "UNASSIGNED": "image", "RETRY_SECONDS": "30"}


def server_down() -> str:
    return (_compose("down").stderr or "stopped").strip()


def _docker_logs(container: str, tail: int, since: str = "") -> list[str]:
    cmd = ["docker", "logs", "--tail", str(tail)]
    if since:
        cmd += ["--since", since]
    cmd.append(container)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    return (proc.stdout + proc.stderr).splitlines()


# How far back to look for machines on the provisioning network. A machine that
# has not been heard from within this window has finished, been powered off, or
# rebooted into the image it just received -- in every case it is no longer
# imaging, and leaving it on screen turns the list into a boot history that only
# ever grows. Deriving this from the log window rather than a timestamp in the
# line is deliberate: dnsmasq's own prefix carries no year, so anything parsed
# out of it breaks across a new year and around a restart.
CLIENT_WINDOW = "15m"


# nginx access-log line: IP ... "GET /path HTTP/1.1" status bytes
_NGINX_RE = re.compile(r'^(\S+) \S+ \S+ \[[^\]]*\] "GET (/\S*) HTTP/[^"]*" (\d{3}) (\d+)')


def server_clients() -> list[dict]:
    """Machines active on the provisioning network right now.

    Merges dnsmasq (PXE/DHCP/TFTP) and nginx (imager + image downloads) logs.
    Only the last CLIENT_WINDOW is considered, and a machine that has finished
    downloading its image is dropped: it is about to reboot into that image and
    is no longer a machine waiting to be provisioned. Live progress for a
    machine mid-write belongs to the imaging registry, which the machine itself
    reports into -- this list answers the narrower question of who is on the
    network and needs an image assigned.
    """
    seen: dict[str, dict] = {}
    for line in _docker_logs("debian-ab-dnsmasq", 400, since=CLIENT_WINDOW):
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
    for line in _docker_logs("debian-ab-http", 300, since=CLIENT_WINDOW):
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
    return [e for e in seen.values() if e["event"] != "imaged"]
