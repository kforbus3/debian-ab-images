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

Updates are distributed as signed RAUC bundles. You need a signing keypair:

```bash
# CA + signing cert (keep the keys safe; ship the CA cert as the device keyring)
openssl req -x509 -newkey rsa:4096 -nodes -keyout ca.key.pem -out ca.cert.pem \
    -days 3650 -subj "/CN=Debian A/B CA"
```

Put the CA certificate at `/etc/rauc/keyring.pem` in the image (replace the
placeholder) so devices verify bundles. Then build a bundle from a new root
filesystem image and install it on a device:

```bash
rauc bundle --cert=cert.pem --key=key.pem update-1.1.raucb
rauc install update-1.1.raucb       # writes the inactive slot
reboot                              # boots the updated slot; rolls back on failure
```

> The repository focuses on building and mass-deploying the base image. Full RAUC
> bundle authoring (root images, hooks, signing infrastructure) is an operational
> step layered on top; the image is configured so RAUC works once you supply a
> keyring and bundles.

## Manual slot switch (without RAUC)

```bash
grub-editenv /boot/grub/grubenv set ORDER="B A" B_TRY=0
reboot
```
