# Atomic Updates (RAUC + A/B)

The image ships A/B-ready: two root slots and GRUB slot selection via `grubenv`,
with [RAUC](https://rauc.io/) preconfigured. This lets you update the **inactive**
slot and flip to it atomically, with automatic rollback if the new slot fails to
boot.

## How slot selection works

`grubenv` (on the `BOOT` partition) holds:

- `ORDER` — slot priority, e.g. `A B`
- `A_TRY` / `B_TRY` — per-slot boot attempt counters

GRUB boots the first slot in `ORDER` whose `TRY` is `0`, setting it to `1` first.
On a successful boot, `ab-mark-good.service` (a oneshot that runs at
multi-user) resets the booted slot's counter via `grub-editenv`; a boot that
never gets that far leaves the counter at `1`, so the next boot falls through
to the other slot — the basis for safe rollback.

## Inspecting state

```bash
rauc status                  # show slots and the active/booted slot
grub-editenv /boot/grub/grubenv list
```

## Producing update bundles

Updates are distributed as signed RAUC bundles, built from an image you have
already built. In the web UI: **Updates → Build bundle**. On the command line:

```bash
docker run --rm --privileged -v "$PWD/output":/output \
    --entrypoint /build/make-bundle.sh debian-ab-builder:amd64 \
    --image /output/debian-trixie-ab.img
```

A bundle carries the **root filesystem only**. `/boot`, the ESP and the overlay
are left alone — the overlay is the machine's data and identity, and destroying
it on update is the bug this project has already fixed once.

### The signing key, and why order matters

RAUC installs a bundle only if it is signed by a certificate **already inside the
image**. The first bundle build generates `output/rauc-keys/{key.pem,cert.pem}`,
and `build-image.sh` installs `cert.pem` into every image it builds afterwards.

So: build a bundle once to create the key, then **rebuild your images**. A
machine imaged before the key existed can never accept a bundle signed by it —
there is no way to add trust to a deployed machine short of re-imaging it.

Keep `output/rauc-keys/` and keep it private. Losing `key.pem` means no further
updates for machines already deployed; leaking it means someone else can sign an
update your fleet will install.

### Source images

- **Uncompressed** (`.img`, not `.img.zst`) — the builder mounts the root slot
  out of it.
- **Compressed images are fine.** The builder decompresses first, because the
  root slot has to be mounted out of the image and that cannot be done through
  zstd or gzip. It costs a few minutes and room for the expanded image.
- **Encrypted images are fine**, but the builder needs the passphrase to read
  the root slot: `--luks-passphrase` on the command line, or the field that
  appears on the Updates page when you pick an encrypted image. It is used only
  while building. What goes into the bundle is the plain filesystem, so a bundle
  built from an encrypted image is not itself encrypted and installs on
  encrypted and unencrypted machines alike — each machine writes into its own
  already-unlocked slot.

Bundles land in `output/bundles/` and are served at `http://<server>/bundles/`.
The newest is recorded in `output/bundles/latest`, which is what `ab-update`
reads when given no arguments.

## Installing on a machine

```bash
ab-update                                   # newest bundle from the server it was imaged from
ab-update http://10.0.0.1/bundles/x.raucb   # a specific bundle
ab-update --status                          # which slot is running, and what is on the other
systemctl reboot
```

`ab-update` with no URL asks the provisioning server this machine was imaged
from — the address the imager left in `/boot/ab-deploy.json`. A machine written
to a disk by hand has no such file and needs the URL given explicitly.

Nothing about the running system changes until the reboot.

## The kernel, and what a bundle still does not update

A bundle carries the root filesystem **and** the kernel and initramfs.

`/boot` is one shared partition, so each slot has its own kernel under a fixed
name — `/boot/A/vmlinuz` and `/boot/B/vmlinuz`, with matching `initrd.img`. An
update writes only the inactive slot's pair, so the kernel the machine is
currently running is never touched and a rollback lands on a slot whose kernel
and root filesystem still match each other. Because the names carry no version,
`grub.cfg` never has to change.

The versioned files `dpkg` installs (`/boot/vmlinuz-6.12.x`) stay where they
are; they are what `update-initramfs` writes to. Anything that regenerates the
initramfs on a running machine must therefore copy it into the slot's directory
or GRUB will not load it:

```bash
ab-sync-boot            # copy this machine's current kernel+initramfs into its slot
```

`luks-enroll` already does this — it regenerates the initramfs so the machine
can unlock via TPM or Tang, and without the copy the machine would come back
asking for a passphrase.

**Still not updated by a bundle:** `grub.cfg` itself, the GRUB binaries in the
ESP, and the partition layout. Changing those needs a re-image.

## apt on a deployed machine

apt works normally. Nothing is held, pinned, or blocked, and packages install
the way they do anywhere else. Two things behave differently, both about the
boot path:

- **`update-grub` does nothing.** It is diverted to a no-op, because
  regenerating `grub.cfg` from `/etc/grub.d` would drop slot selection, the
  `rauc.slot=` parameters and the recovery entries — turning a working machine
  into one that boots until the first time you need to roll back. Debian calls
  `update-grub` from the kernel postinst and from the grub packages' own
  postinst, so a single `apt upgrade` would otherwise do it. The original is
  kept as `/usr/sbin/update-grub.distrib`.
- **A kernel installed by apt is never booted.** It lands at
  `/boot/vmlinuz-<version>`; GRUB boots `/boot/<A|B>/vmlinuz`, which only a
  bundle replaces. The install succeeds and prints a notice saying so. Changing
  the kernel means building a new image and shipping it as a bundle — a kernel
  swapped in underneath a running slot would no longer match the root filesystem
  it was built against, and the rollback slot would be all that stood between
  you and an unbootable machine.

### Packages do not survive an update

More important than either: **anything installed with apt is removed by the next
A/B update.** The root filesystem is an overlay, so apt's changes land in the
machine's upper layer — and on a slot change the OS-owned paths (`/usr`, `/bin`,
`/lib`, dpkg's and apt's state) are cleared, because a package database from the
old release shadowing the new one is how an update silently half-applies.

So apt is fine for looking around, debugging, or something temporary. For
software that should persist, put it in the image: `--packages` at build time,
or `overlay.d/` and `--run-script` (see [BUILDER.md](BUILDER.md)). Machine state
outside those paths — `/home`, `/etc`, `/srv`, `/opt`, `/usr/local`, logs — is
kept.

`/usr/local` is deliberately excluded from the clear even though `/usr` is not.
The FHS reserves it for locally installed software precisely because packages
never touch it, and it is where a person puts a script expecting it to stay.

**In practice this means your patch cadence is your image-rebuild cadence.** The
`apt-get upgrade` happens inside the build, so rebuilding picks up whatever is
current in the archive, and the bundle ships it to the fleet.

## Watching a rollout

The **Fleet** page lists every machine this server has imaged, what it is
running, and whether it reported back after booting. A machine shown as
`never-booted` finished imaging and was never heard from again — the case the
imager's own reports cannot cover, because the last of them is sent before the
reboot.

**Updates** shows the versions running across the fleet, so you can tell whether
a bundle has been rolled out or merely built.

## When an update is refused

`rauc install` fails and the running system is untouched. Two common causes:

| symptom | cause |
| --- | --- |
| signature verification failed | signed by a key this image does not trust — the certificate has to be inside the image when it is built |
| compatible mismatch | built from a different distribution; `compatible` is `<distro>-ab`, which is what stops a Debian bundle installing onto Ubuntu |

If an update installs but a file from it seems not to have arrived, an older
copy in the overlay is shadowing it. `ab-overlay-diff` on the machine says which
files those are; see [RECOVERY.md](RECOVERY.md).

## Manual slot switch (without RAUC)

```bash
grub-editenv /boot/grub/grubenv set ORDER="B A" B_TRY=0
reboot
```
