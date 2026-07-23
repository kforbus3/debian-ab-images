# Image Builder

Builds a bootable Debian or Ubuntu A/B disk image in a privileged Docker container.

## Usage

```bash
make image HOSTNAME=node USERNAME=admin PASSWORD='ChangeMe123'
# Ubuntu: make image SUITE=noble ...
# or directly:
./builder/run.sh --hostname node --username admin --password 'ChangeMe123' \
    --root-size 3072 --compress zstd
./builder/run.sh --suite noble --hostname node --username admin --password 'ChangeMe123'
```

Output lands in `./output/` (e.g. `debian-trixie-ab.img.zst`, `ubuntu-noble-ab.img.zst`).

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--distro` | auto | `debian` \| `ubuntu`; auto-detected from `--suite` |
| `--suite` | `trixie` | Release: `trixie`, `bookworm` (Debian); `resolute`, `noble`, `jammy` (Ubuntu) |
| `--mirror` | distro default | APT mirror (`deb.debian.org` / `archive.ubuntu.com`) |
| `--arch` | `amd64` | Target architecture |
| `--hostname` | `debian-ab` | Image hostname |
| `--username` | `debian` | Login user (added to `sudo`) |
| `--password` | `debian` | Password for that user |
| `--root-size` | `3072` | MiB per root slot |
| `--image-size` | `auto` | Total image size in GiB; `auto` = smallest possible |
| `--packages "a b"` | — | Extra packages to install |
| `--ssh-pubkey FILE` | — | Install an authorized SSH key for the user (from a file) |
| `--ssh-authorized-key K` | — | Same, passing the key inline |
| `--ssh-key-only` | off | Disable SSH password auth (requires a key) |
| `--encrypt` | off | LUKS2-encrypt the root slots and overlay |
| `--unlock METHOD` | `keyfile` | Auto-unlock: `passphrase` \| `keyfile` \| `tpm2` \| `tang` |
| `--luks-passphrase P` | — | LUKS passphrase (setup + recovery); required with `--encrypt` |
| `--tang-url URL` | — | Tang server URL (required for `--unlock tang`) |
| `--compress` | `zstd` | `zstd` \| `gzip` \| `none` |
| `--output PATH` | `/output/<distro>-<suite>-ab.img` | Output path (inside the container) |

By default the image is built as small as the layout allows (two root slots +
boot + a minimal overlay, ≈7 GiB raw with the defaults) and the overlay
partition auto-expands to fill the real disk on first boot — one image deploys
to any larger disk, and smaller images write faster during mass imaging.

## What's in the image

- Minimal Debian/Ubuntu (`minbase`) + kernel, GRUB, OpenSSH, sudo, RAUC, growpart.
- A login user with sudo; **root is locked** (log in as the user, use sudo).
- `systemd-networkd` configured for DHCP on all ethernet interfaces.
- RAUC preconfigured (`/etc/rauc/system.conf`, `compatible=<distro>-ab`,
  i.e. `debian-ab` or `ubuntu-ab` — update bundles must match).
- `first-boot-expand.service` to grow the overlay on first boot.
- `ab-mark-good.service` — resets the booted slot's GRUB try counter once the
  system reaches multi-user, so the A/B fallback logic stays armed (see
  [UPDATES.md](UPDATES.md)).
- `machine-identity.service` — the image ships with a **blank `machine-id` and
  no SSH host keys** (so imaged machines aren't identity clones of each other).
  On first boot each machine generates its own and stores them in the persistent
  overlay, so they survive A/B slot switches and updates.
- Each image ships with `<image>.sha256` (verified by the netboot imager) and a
  `<image>.json` metadata sidecar (distro, release, sizes, encryption) consumed
  by the web UI.

Ubuntu images use the `linux-image-generic` kernel, which pulls in
`linux-firmware` — expect a noticeably larger rootfs than Debian; the default
3072 MiB root slots still fit it comfortably.

## Customization

- **More packages:** `make image PACKAGES="qemu-guest-agent vim curl"` (or
  `--packages "qemu-guest-agent vim curl"` when calling the script directly).
  Also exposed as the "Extra packages" field in the web UI's build form.
- **Bake in files/config:** add them under `builder/overlay/` — its `etc/` and
  `usr/` trees are copied into the image. (Static files only; per-build values
  like hostname/user are handled by the script.)
- **Different base:** `--suite bookworm`, `--suite resolute` (Ubuntu 26.04),
  `--suite noble` (Ubuntu 24.04), `--suite jammy` (Ubuntu 22.04).
- **SSH-key-only login:** pass `--ssh-pubkey` and set a strong throwaway password.

## How it runs

`builder/run.sh` builds `builder/Dockerfile` and runs it `--privileged` (needed for
loop devices and mounts) with `./output` mounted. The host must be Linux-capable
for loop devices; on Docker Desktop this works inside the Docker VM.

## SSH access

By default the image runs `sshd` and allows password login for the created user
(`root` is locked). To lock it down:

```bash
--ssh-authorized-key "ssh-ed25519 AAAA… you@host" --ssh-key-only
```

`--ssh-key-only` drops a `sshd_config.d` snippet that sets
`PasswordAuthentication no` (so you must supply a key).

## Disk encryption (LUKS2)

`--encrypt` puts the two root slots **and** the overlay inside LUKS2 containers
(the shared `/boot` stays plaintext so GRUB can load the kernel). Pick how each
machine unlocks at boot with `--unlock`:

| Method | Auto-unlock | Key on disk? | Use when |
|--------|-------------|--------------|----------|
| `tpm2` | ✅ (sealed to the TPM) | ❌ | Targets have a TPM 2.0 — **most secure auto-unlock** |
| `tang` | ✅ (from a Tang server) | ❌ | No TPM, but a trusted LAN — **best no-TPM auto-unlock** |
| `keyfile` | ✅ (key in initramfs) | ⚠️ yes | Anywhere, but weak at-rest protection — convenience only |
| `passphrase` | ❌ (prompt at boot) | ❌ | Maximum security, attended boots |

The passphrase you pass is always enrolled as a **recovery** key.

**How tpm2/tang stay unattended *and* keyless:** the image ships with a bootstrap
keyfile in the initramfs so the very first boot unlocks on its own. A first-boot
service (`luks-enroll`) then binds the volumes to the TPM (or Tang), rebuilds the
initramfs, and **destroys the bootstrap keyfile** — so after first boot no key
remains on disk. If enrollment fails (e.g. no TPM, Tang unreachable) it keeps the
keyfile and retries next boot, so a machine never bricks.

```bash
# TPM2 (recommended where available; UNLOCK defaults to tpm2)
make image ENCRYPT=1 LUKS_PASSPHRASE='recover-me'
# or: ./builder/run.sh --encrypt --unlock tpm2 --luks-passphrase 'recover-me'

# Tang / NBDE
make image ENCRYPT=1 UNLOCK=tang TANG_URL=http://tang.lan:7500 LUKS_PASSPHRASE='recover-me'
# or: ./builder/run.sh --encrypt --unlock tang --tang-url http://tang.lan:7500 --luks-passphrase 'recover-me'
```

> The overlay auto-expand on first boot resizes the LUKS container too.

## Notes & limitations

- **Boots on both BIOS and UEFI.** GRUB is installed twice: `i386-pc` into the
  bios_grub partition, and `x86_64-efi` onto the ESP at the removable path
  (`\EFI\BOOT\BOOTX64.EFI`, no NVRAM entry needed — right for mass imaging).
  Both share the same `grub.cfg` and `grubenv` on the BOOT partition, so A/B
  slot logic behaves identically under either firmware.
- **Secure Boot is not supported** (GRUB is unsigned) — disable it on UEFI
  targets, or sign the bootloader yourself.
- `/boot` and the kernel are shared across A/B; A/B applies to the root
  filesystem. A bad kernel affects both slots — test kernel changes before
  rolling out. See [UPDATES.md](UPDATES.md).
