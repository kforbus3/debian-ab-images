# Image Builder

Builds a bootable Debian A/B disk image in a privileged Docker container.

## Usage

```bash
make image HOSTNAME=node USERNAME=admin PASSWORD='ChangeMe123' IMAGE_SIZE=8
# or directly:
./builder/run.sh --hostname node --username admin --password 'ChangeMe123' \
    --image-size 8 --root-size 3072 --compress zstd
```

Output lands in `./output/` (e.g. `debian-trixie-ab.img.zst`).

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--suite` | `trixie` | Debian suite (e.g. `bookworm`, `trixie`) |
| `--arch` | `amd64` | Target architecture |
| `--hostname` | `debian-ab` | Image hostname |
| `--username` | `debian` | Login user (added to `sudo`) |
| `--password` | `debian` | Password for that user |
| `--root-size` | `3072` | MiB per root slot |
| `--image-size` | `8` | Total image size in GiB |
| `--packages "a b"` | — | Extra packages to install |
| `--ssh-pubkey FILE` | — | Install an authorized SSH key for the user |
| `--compress` | `zstd` | `zstd` \| `gzip` \| `none` |
| `--output PATH` | `/output/debian-<suite>-ab.img` | Output path (inside the container) |

The image auto-expands its overlay partition to fill the real disk on first boot,
so build a compact image (e.g. 8 GiB) and deploy it to any larger disk.

## What's in the image

- Minimal Debian (`minbase`) + kernel, GRUB, OpenSSH, sudo, RAUC, growpart.
- A login user with sudo; **root is locked** (log in as the user, use sudo).
- `systemd-networkd` configured for DHCP on all ethernet interfaces.
- RAUC preconfigured (`/etc/rauc/system.conf`, `compatible=debian-ab`).
- `first-boot-expand.service` to grow the overlay on first boot.

## Customization

- **More packages:** `--packages "qemu-guest-agent vim curl"`.
- **Bake in files/config:** add them under `builder/overlay/` — its `etc/` and
  `usr/` trees are copied into the image. (Static files only; per-build values
  like hostname/user are handled by the script.)
- **Different base:** `--suite bookworm`.
- **SSH-key-only login:** pass `--ssh-pubkey` and set a strong throwaway password.

## How it runs

`builder/run.sh` builds `builder/Dockerfile` and runs it `--privileged` (needed for
loop devices and mounts) with `./output` mounted. The host must be Linux-capable
for loop devices; on Docker Desktop this works inside the Docker VM.

## Notes & limitations

- BIOS/GRUB boot (covers most servers and VMs). UEFI-only targets need an ESP +
  `grub-efi`; not included by default.
- `/boot` and the kernel are shared across A/B; A/B applies to the root
  filesystem. A bad kernel affects both slots — test kernel changes before
  rolling out. See [UPDATES.md](UPDATES.md).
