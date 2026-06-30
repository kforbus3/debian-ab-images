# Debian A/B Images

Build a Debian A/B (dual-root) disk image once, then **netboot a whole switch full
of machines and image them all at once** — unattended. Designed for IT departments
and homelabs that need to provision many identical machines quickly and reliably.

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)

---

## What you get

| Component | What it does |
|-----------|--------------|
| **builder/** | Produces a bootable Debian A/B disk image (`.img`) — two root slots, shared `/boot`, persistent overlay, GRUB+RAUC for atomic updates, first-boot auto-expand. Runs in Docker. |
| **imager/** | Builds a tiny netboot environment (kernel + initramfs) that auto-detects a machine's disk, streams the image over HTTP, writes it, and reboots. |
| **server/** | A Dockerized provisioning server: dnsmasq (proxyDHCP/DHCP + TFTP + iPXE) and nginx (serves the image). Plug machines into the switch, power on, walk away. |

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
  p1  bios_grub   1 MiB    GRUB core
  p2  BOOT        512 MiB  shared /boot, kernel, grubenv   (label BOOT)
  p3  rootfs-a    N GiB    root slot A (Debian)            (label rootfs-a)
  p4  rootfs-b    N GiB    root slot B (copy of A)         (label rootfs-b)
  p5  overlay     rest     persistent data /var/lib/overlay (grows on first boot)
```

- **A/B roots** let you update atomically: write the inactive slot, flip the
  GRUB boot order, reboot. Both slots are populated at build time.
- **GRUB + RAUC** integration: slot selection lives in `grubenv`; [RAUC](https://rauc.io/)
  is preconfigured (`compatible=debian-ab`) for signed bundle updates.
- **Persistent overlay** survives slot updates and **auto-expands** to fill the
  target disk on first boot — so the same image works on any disk size.

## Quick start

### 1. Build the image

```bash
make image HOSTNAME=node USERNAME=admin PASSWORD='ChangeMe123' IMAGE_SIZE=8
# → output/debian-trixie-ab.img.zst
```

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
and reboots into Debian A/B — no keyboard required. Watch progress with:

```bash
make server-logs
```

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

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together
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
