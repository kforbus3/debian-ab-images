# Recovery: the overlay, and how to get out from under it

**In a hurry?** Reboot, hold <kbd>Shift</kbd> (BIOS) or press <kbd>Esc</kbd>
(UEFI) to get the GRUB menu, and pick one of the **Recovery** entries. Nothing
you choose there deletes anything.

---

## Why this page exists

The root filesystem is an overlay:

```
/            overlay
  lower      the A/B root slot, read-only, exactly as the imager wrote it
  upper      /var/lib/overlay/upper — every write since the machine was imaged
```

The upper layer is **shared by both slots**. That is deliberate: an A/B update
replaces the OS in the inactive slot and reboots into it, and if the upper layer
were tied to a slot, every update would take `/home` with it.

The cost is that a change made while running slot A is still there in slot B.
So the usual instinct — "something is broken, boot the other slot" — does not
help on its own if the problem is a file you changed rather than a file the
image shipped. These recovery entries are what makes that work again.

On a slot change the initramfs does clear the paths the OS owns
(`/usr`, `/bin`, `/sbin`, `/lib*`, `/boot`, dpkg and apt state), so an update
cannot be shadowed by binaries from the previous release. **`/etc` is not
cleared** — machine configuration is meant to survive updates — which is the
most common way to end up here.

## Step 1: see what changed

From the running system:

```console
$ ab-overlay-diff
Machine changes since imaging  (slot A, upper layer /var/lib/overlay/upper)

Shadowing a file from the image (2)
  /etc/fstab
  /etc/systemd/network/10-wired.network

2 shadowing the image, 47 added, 0 deleted
(-a lists the added and deleted ones too)
```

"Shadowing" is the list that matters: those files take precedence over the image
on **both** slots. An A/B update cannot replace them, and booting the other slot
will not leave them behind.

`ab-overlay-diff -a` adds files this machine created (normal — `/home`, logs,
SSH host keys) and files it deleted from the image.

## Step 2: choose a recovery entry

Reboot and pick from the GRUB menu. Both normal entries stay at the top and are
what boots automatically; the recovery entries are only ever reached by choosing
them.

### Recovery: Slot A|B, reset writable state (keeps a copy)

Boots with **empty** writable state. Each store the image uses is renamed
alongside itself: `upper` → `upper.prev`, `persist` → `persist.prev`, `slots` →
`slots.prev`. All of them, not just the overlay's upper layer — a machine told
to start clean that came back up on its old `/var` and `/home` would be the
opposite of what the entry promises.

- It is a rename on the same filesystem: instant, and it copies nothing, so it
  needs no free space.
- **Nothing is deleted.** Every file is still there under `upper.prev`, and
  `/var/lib/overlay` is mounted in the booted system, so you can copy things
  back out of it.
- Applies to **one boot**. Nothing is written to `grubenv`. The next normal boot
  uses the fresh upper layer.
- Expect the machine to look newly imaged: no `/home` contents, default `/etc`.
  Your data is in `upper.prev`, not gone.

Recovering something afterwards:

```console
# ls /var/lib/overlay/upper.prev
# cp -a /var/lib/overlay/upper.prev/home/alice /home/
```

Putting the whole thing back, if the reset did not help:

```console
# rm -rf /var/lib/overlay/upper
# mv /var/lib/overlay/upper.prev /var/lib/overlay/upper
# reboot
```

If a `*.prev` already exists, a second reset is **refused** rather than run —
overwriting it would destroy the state you were trying to rescue. Move or remove
it first, or use the "image as written" entry to get in and tidy up. The refusal
is printed to the kernel log (`dmesg | grep ab-overlay`).

Machine identity — SSH host keys and `machine-id` — lives in
`/var/lib/overlay/persistent/`, which is outside every store and is **not** set
aside by a reset. A recovered machine keeps its identity on purpose.

### Recovery: Slot A|B, image as written (no writable state)

Boots the root slot directly and applies no state manifest at all. This is the
diagnostic: what you get is exactly what the imager wrote, so

- if the problem is **gone**, it lives in the overlay → use the reset entry;
- if the problem is **still there**, it is in the image → rebuild or update.

The overlay partition is untouched and not mounted at `/`, so you can mount it
by hand to repair a single file without discarding everything else:

```console
# mkdir -p /mnt/ovl && mount /dev/disk/by-label/overlay /mnt/ovl   # unencrypted
# cryptsetup open /dev/disk/by-partlabel/overlay ovl && \
  mount /dev/mapper/ovl /mnt/ovl                                    # encrypted
# rm /mnt/ovl/upper/etc/fstab      # drop one shadowing file
# reboot
```

Note this entry gives you a writable root **on the slot itself**, so anything
you change lands in the image copy and will be replaced by the next A/B update.
Use it to repair the writable state, not as a place to work.

## Doing it by hand

The menu entries are just kernel command-line flags. At the GRUB menu press
<kbd>e</kbd>, add one to the `linux` line, then <kbd>Ctrl</kbd>+<kbd>X</kbd>:

| flag | root slot | writable state |
| --- | --- | --- |
| `ab.state=reset` | manifest applied as normal | every store set aside as `*.prev`, fresh ones created |
| `ab.state=off` | booted directly | untouched, not mounted at `/` |
| *(none)* | manifest applied as normal | used as-is |

Neither flag persists. `ab.overlay=reset` and `ab.overlay=off` are still
accepted, so a runbook written against an older image keeps working.

The "image as written" menu entries also append `rw`. Under the `stateful` and
`appliance` models the slot is normally read-only and everything writable comes
from the manifest — which is exactly what this entry switches off, so without it
the recovery boot would land you on a system with nowhere to write anything.

## Checking what happened

The initramfs logs every decision to the kernel ring buffer:

```console
$ dmesg | grep ab-overlay
ab-overlay: upper layer set aside as /var/lib/overlay/upper.prev; starting clean
ab-overlay: root is now an overlay (lower=slot A, upper=overlay partition)
```

Other lines you may see, all of which mean the machine booted the slot directly
rather than failing:

| message | meaning |
| --- | --- |
| `disabled on the command line` | `ab.state=off` (or `ab.overlay=off`) was passed |
| `no overlay device` | the overlay partition was not found or not unlocked |
| `overlay filesystem would not mount` | the partition is there but its filesystem would not mount |
| `reset refused: ... already exist(s)` | a snapshot is already saved; deal with it first |
| `REFUSING: this image uses state model ...` | the image's writable-state layout differs from the one the machine was imaged with; it needs a re-image, not an update |

## What this does not touch

Neither recovery entry writes to the root slots, the LUKS headers, the partition
table, or `grubenv`. The A/B slot order and try-counters are unaffected, so
recovery does not interfere with a pending update or a rollback.
