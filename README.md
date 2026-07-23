# Debian A/B Images

Build a Debian or Ubuntu A/B (dual-root) disk image once, then **netboot a whole
switch full of machines and image them all at once** — unattended. Designed for IT
departments and homelabs that need to provision many identical machines quickly
and reliably.

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)

---

## What you get

| Component | What it does |
|-----------|--------------|
| **builder/** | Produces a bootable Debian **or Ubuntu** A/B disk image (`.img`) — two root slots, shared `/boot`, persistent overlay, GRUB+RAUC for atomic updates, first-boot auto-expand. Runs in Docker. |
| **imager/** | Builds a tiny netboot environment (kernel + initramfs) that auto-detects a machine's disk, streams the image over HTTP, writes it, and reboots. |
| **server/** | A Dockerized provisioning server: dnsmasq (proxyDHCP/DHCP + TFTP + iPXE) and nginx (serves the image). Plug machines into the switch, power on, walk away. |
| **webui/** | An optional browser UI to manage everything — build images with a **live log**, manage the image library, configure/run the provisioning server, and **watch machines being imaged in real time**. |

```
                              ┌─────────── provisioning server (Docker) ───────────┐
   [ switch ]                 │  dnsmasq  ── proxyDHCP/DHCP + TFTP + iPXE chainload  │
   machine 1  ──PXE boot──▶   │  nginx    ── serves imager kernel/initramfs + image │
   machine 2  ──PXE boot──▶   └────────────────────────────────────────────────────┘
   machine N  ──PXE boot──▶          │
        ▲                            ▼
        │                    each machine boots the imager, which
        └──── reboots ◀──────  writes the A/B image to its local disk
              into Debian A/B
```

## The A/B image layout

```
GPT:
  p1  bios_grub   1 MiB    GRUB BIOS core
  p2  ESP         128 MiB  EFI system partition (GRUB UEFI)  (label EFI)
  p3  BOOT        512 MiB  shared /boot, kernel, grubenv     (label BOOT)
  p4  rootfs-a    N GiB    root slot A (Debian/Ubuntu)       (label rootfs-a)
  p5  rootfs-b    N GiB    root slot B (copy of A)           (label rootfs-b)
  p6  overlay     rest     persistent data /var/lib/overlay  (grows on first boot)
```

- **Boots on BIOS and UEFI** — GRUB is installed for both (`i386-pc` in the
  bios_grub partition, `x86_64-efi` at the removable path `\EFI\BOOT\BOOTX64.EFI`),
  so the same image works on legacy and modern firmware. Secure Boot must be
  disabled on UEFI machines.
- **A/B roots** let you update atomically: write the inactive slot, flip the
  GRUB boot order, reboot. Both slots are populated at build time.
- **GRUB + RAUC** integration: slot selection lives in `grubenv`; [RAUC](https://rauc.io/)
  is preconfigured (`compatible=<distro>-ab`) for signed bundle updates.
- **Smallest possible image** by default: the image is sized to its contents and
  the **persistent overlay auto-expands** to fill the target disk on first boot —
  so one image works on any disk size, and imaging is as fast as possible.
- **Unique machine identity**: the image ships with a blank `machine-id` and no
  SSH host keys; each machine generates its own on first boot and keeps them
  across A/B updates (stored in the overlay).

## Quick start

### 1. Build the image

```bash
make image HOSTNAME=node USERNAME=admin PASSWORD='ChangeMe123'
# → output/debian-trixie-ab.img.zst

# Ubuntu instead? Pick an Ubuntu release with SUITE:
make image SUITE=noble HOSTNAME=node USERNAME=admin PASSWORD='ChangeMe123'
# → output/ubuntu-noble-ab.img.zst

# Need extra packages baked into the image? Add PACKAGES:
make image PACKAGES="qemu-guest-agent vim curl" HOSTNAME=node
```

The image is built as small as possible by default (about 7 GiB raw with the
default slot sizes, far less compressed) and expands to fill each machine's
disk on first boot; set `IMAGE_SIZE=<GiB>` to force a fixed size.

Supported releases: Debian `trixie` (13) and `bookworm` (12); Ubuntu `resolute`
(26.04 LTS), `noble` (24.04 LTS), and `jammy` (22.04 LTS).

### 2. Build the netboot imager

```bash
make imager
# → output/imager/{vmlinuz,initramfs.img}
```

### 3. Start the provisioning server

```bash
cd server
cp .env.example .env
# Edit .env: set SERVER_IP, IMAGE_FILE, and MODE (proxy or dhcp).
docker compose up -d --build
```

### 4. Image the machines

Plug the target machines into the same switch, set them to **network boot** (PXE),
and power them on. Each one boots the imager, writes the image to its local disk,
and reboots into the A/B system — no keyboard required. Watch progress with:

```bash
make server-logs
```

## Web UI (manage everything from the browser)

Prefer a UI over the command line? Run the management console:

```bash
cd webui
cp .env.example .env      # set ADMIN_PASSWORD, SECRET_KEY, and HOST_PROJECT_DIR
                          # (absolute path to this repo)
docker compose up -d --build
```

Open **http://localhost:8080** to build images (with a live build log), manage the
image library, configure and start the provisioning server, and watch machines get
imaged in real time. See [docs/WEBUI.md](docs/WEBUI.md).

## DHCP modes

Set `MODE` in `server/.env`:

- **`proxy`** (default) — *coexists* with an existing DHCP server/router on the
  LAN via proxyDHCP. It only answers PXE boot questions; your router still hands
  out IPs. Best for most homelab/office networks.
- **`dhcp`** — *standalone*. The server runs full DHCP and hands out IPs itself.
  Best for an isolated/dedicated provisioning switch.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full configuration.

## Safety

Network imaging **overwrites the target disk**. The imager selects the largest
non-removable disk by default; pin a specific disk with `imager.disk=/dev/sdX`
(see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)). Only run the provisioning server on a
network where you intend every PXE-booting machine to be re-imaged.

## Documentation

- **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) — installation and user guide (start here)**
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together
- [docs/WEBUI.md](docs/WEBUI.md) — the browser-based management console
- [docs/BUILDER.md](docs/BUILDER.md) — image build options and customization
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — provisioning server, DHCP modes, real-hardware + QEMU testing
- [docs/UPDATES.md](docs/UPDATES.md) — RAUC atomic updates and A/B slot switching
- [docs/SECURITY.md](docs/SECURITY.md) — secrets, signing, network exposure
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Requirements

- A Linux host with Docker (the builder needs `--privileged` for loop devices).
- For the provisioning server: a host on the imaging LAN (host networking).

## License

Licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
