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
| `--root-size` | `3072` | MiB per root slot (raised to a per-distro minimum — see [Root slot sizing](#root-slot-sizing)) |
| `--image-size` | `auto` | Total image size in GiB; `auto` = smallest possible |
| `--packages "a b"` | — | Extra packages to install |
| `--ssh-pubkey FILE` | — | Install an authorized SSH key for the user (from a file) |
| `--ssh-authorized-key K` | — | Same, passing the key inline |
| `--ssh-key-only` | off | Disable SSH password auth (requires a key) |
| `--encrypt` | off | LUKS2-encrypt the root slots and overlay |
| `--unlock METHOD` | `keyfile` | Auto-unlock: `passphrase` \| `keyfile` \| `tpm2` \| `tang` |
| `--luks-passphrase P` | — | LUKS passphrase (setup + recovery); required with `--encrypt` |
| `--luks-passphrase-file F` | — | Read it from a file (or `-` for stdin) instead — keeps it out of `ps` |
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

### Root slot sizing

Each of the two root slots holds a complete OS, so the slot — not the image — is
the binding constraint. The builder enforces a per-distro minimum and raises
`--root-size` if you ask for less:

| Distro | Minimum root slot | Why |
|--------|-------------------|-----|
| Debian | 2560 MiB | base system + `linux-image-amd64` + initramfs |
| Ubuntu | 5120 MiB | `linux-image-generic` **depends on** `linux-firmware` and `linux-modules-extra` (~1.7 GiB installed), which Debian never pulls in |

Ubuntu needs roughly twice Debian's space for the same install. At Debian's
default of 3072 MiB an Ubuntu build fills the slot partway through initramfs
generation and dies with a bare `No space left on device` from `cpio` — measured,
not theoretical. The minimum is now enforced up front, and if the slot fills
anyway the builder reports the actual usage instead of leaving you to read dpkg
output.

APT's downloaded `.deb` archives are bind-mounted outside the root slot during
installation, so Ubuntu's ~460 MiB of package downloads no longer count against
it.

## Customization

- **More packages:** `make image PACKAGES="qemu-guest-agent vim curl"` (or
  `--packages "qemu-guest-agent vim curl"` when calling the script directly).
  Also exposed as the "Extra packages" field in the web UI's build form.
- **Bake in files/config:** put them under **`overlay.d/`** at the top of the
  checkout — see [Shipping your own files](#shipping-your-own-files) below.
  (`builder/overlay/` is the project's own, and only its `etc/` and `usr/` trees
  are copied; keep your files out of it so they are not committed.)
- **Run commands inside the image:** `--run-script FILE`, or the script box in
  the web UI's build form.
- **Different base:** `--suite bookworm`, `--suite resolute` (Ubuntu 26.04),
  `--suite noble` (Ubuntu 24.04), `--suite jammy` (Ubuntu 22.04).
- **SSH-key-only login:** pass `--ssh-pubkey` and set a strong throwaway password.

## Shipping your own files

Anything under `overlay.d/` at the top of the checkout is copied over the
image's root filesystem, keeping its path:

    overlay.d/etc/hosts                   ->  /etc/hosts
    overlay.d/etc/netplan/10-corp.yaml    ->  /etc/netplan/10-corp.yaml
    overlay.d/usr/local/bin/site-check    ->  /usr/local/bin/site-check
    overlay.d/opt/agent/agent.conf        ->  /opt/agent/agent.conf

Unlike `builder/overlay/`, the whole tree is copied, not just `etc/` and `usr/`,
and it is applied afterwards — so your version of a file wins over the project's
default. Everything there except its README is gitignored.

### These files also override what is already on the machine

This is the half that is easy to miss. A deployed machine's root is an overlay,
and a file the machine has written shadows the image's copy — so shipping a new
`/etc/hosts` would install it and the machine would carry on reading its own,
with nothing to say so.

Every file in `overlay.d/` is therefore recorded in the image as **image-owned**
(`/usr/lib/ab/image-owned.list`). On the update that delivers it, the machine's
copy **at that exact path** is dropped, so the image's version is what it reads.

Per file, never per directory: shipping one netplan file does not remove the
machine's others. Same path, image wins; everything else is left alone.

Use `--own-path /etc/resolv.conf` (repeatable, or the field in the web UI) to
claim a path you are not shipping a file for.

What does **not** belong here: per-machine identity — hostname, `machine-id`,
SSH host keys — which is generated on first boot and kept in the overlay on
purpose. This is for fleet-wide configuration that should be part of the image.

### Running commands in the image

`--run-script FILE` runs a shell script inside the chroot after packages and
both overlays are applied:

```bash
#!/bin/bash
set -euo pipefail
systemctl enable my-agent          # writes symlinks: works
usermod -aG dialout admin
echo "site=hq" > /etc/site.conf
```

It runs as root with the image as `/`, but on the builder's kernel — so
`systemctl enable` works while starting a service does not, because there is no
running init. A non-zero exit fails the build rather than shipping a
half-customized image.

## Building for another architecture

Cross-architecture builds run the target's binaries under qemu, which the kernel
only does once an interpreter is registered with `binfmt_misc`. Docker does not
do that on its own, so building an arm64 image or imager on an amd64 host (or
the reverse) needs it registered first.

Both `run.sh` scripts and the web UI now do it automatically before a cross
build:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

It is idempotent and lasts until reboot. The first run on a fresh host pulls
that image, so it needs network; if registration fails the build says so rather
than dying later with `Exec format error`.

Remember that an arm64 **image** needs an arm64 **imager** to be deployed over
the network — the imager is a kernel the target machine executes. Build both.

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

### Storing the passphrase in a secrets manager

With `tpm2`, `tang` or `keyfile`, the passphrase you pass is *only* a recovery
key: nothing types it again, so nothing exercises it until the day a TPM is
cleared by a firmware update and a machine stops at the initramfs prompt. That
is a bad thing to keep in someone's password note.

The web UI can generate it and file it in OpenBao or HashiCorp Vault for you —
see [WEBUI.md](WEBUI.md#secrets-manager). On the command line,
`scripts/luks-secret.sh` does the same against the `bao`/`vault` CLI and writes
the same payload, so an image built either way is recoverable from the other:

```bash
export BAO_ADDR=https://bao.example.lan:8200 BAO_TOKEN=…

# Generate a passphrase, store it, and stage it where the builder can read it
./scripts/luks-secret.sh new debian-trixie-amd64-ab.img

./builder/run.sh --encrypt --unlock tpm2 \
    --luks-passphrase-file /output/.luks-pass \
    --output /output/debian-trixie-amd64-ab.img

./scripts/luks-secret.sh clean          # remove the staged file
```

`new` writes to the store *before* printing anything, and fails if the store
will not take it — so a build never produces an encrypted image whose recovery
key was not persisted first.

Later, to bundle an update from that image (which needs the passphrase to read
the root slot), or simply to recover a machine:

```bash
./scripts/luks-secret.sh stage debian-trixie-amd64-ab.img   # for the builder
./scripts/luks-secret.sh show  debian-trixie-amd64-ab.img   # for a person
```

The passphrase is staged in a file rather than passed as an argument because
arguments are visible in `ps` to every user on the build host; `output/` is
already mounted into the builder, so the file needs no extra plumbing. The
store is keyed on the image name with any `.zst`/`.gz` suffix stripped, so
changing `--compress` does not strand the entry.

Configuration comes from the environment, as both CLIs expect: `BAO_ADDR` /
`VAULT_ADDR`, `BAO_TOKEN` / `VAULT_TOKEN`, plus `LUKS_KV_MOUNT` (default
`secret`) and `LUKS_KV_PREFIX` (default `debian-ab-images`).

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
