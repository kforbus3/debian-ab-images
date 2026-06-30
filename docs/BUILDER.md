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
| `--ssh-pubkey FILE` | — | Install an authorized SSH key for the user (from a file) |
| `--ssh-authorized-key K` | — | Same, passing the key inline |
| `--ssh-key-only` | off | Disable SSH password auth (requires a key) |
| `--encrypt` | off | LUKS2-encrypt the root slots and overlay |
| `--unlock METHOD` | `keyfile` | Auto-unlock: `passphrase` \| `keyfile` \| `tpm2` \| `tang` |
| `--luks-passphrase P` | — | LUKS passphrase (setup + recovery); required with `--encrypt` |
| `--tang-url URL` | — | Tang server URL (required for `--unlock tang`) |
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
# TPM2 (recommended where available)
make image ... ENCRYPT=1   # or: ./builder/run.sh --encrypt --unlock tpm2 --luks-passphrase 'recover-me'

# Tang / NBDE
./builder/run.sh --encrypt --unlock tang --tang-url http://tang.lan:7500 --luks-passphrase 'recover-me'
```

> The overlay auto-expand on first boot resizes the LUKS container too.

## Notes & limitations

- BIOS/GRUB boot (covers most servers and VMs). UEFI-only targets need an ESP +
  `grub-efi`; not included by default.
- `/boot` and the kernel are shared across A/B; A/B applies to the root
  filesystem. A bad kernel affects both slots — test kernel changes before
  rolling out. See [UPDATES.md](UPDATES.md).
