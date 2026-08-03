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

### Two constraints on the source image

- **Uncompressed** (`.img`, not `.img.zst`) — the builder mounts the root slot
  out of it.
- **Unencrypted** — bundling from a LUKS image is not supported yet. Build an
  unencrypted image of the same release to bundle from; encrypted machines can
  still install those bundles, only the packaging side is affected.

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
