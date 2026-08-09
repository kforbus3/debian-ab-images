# Changelog

Notable changes per release. Dates are the tag date.

## Unreleased

**Health checks decide whether an update is kept.** Drop an executable in
`/etc/ab/health.d/` (via `overlay.d`) and it runs before logins are permitted;
if any check fails the slot is never marked good and the next boot rolls back to
the previous release. With no checks installed nothing changes — booting far
enough to permit logins is still the test, which is what it has always been.

This is systemd's own boot-assessment shape: checks are ordered before
`boot-complete.target`, and `ab-mark-good` `Requires=` that target. Checks run
before `systemd-user-sessions.service` so adding them does not reopen the window
that let a reboot switch slots, with a 60s timeout so a hung check cannot
produce a machine nobody can log into. A failed check does not block login — the
machine comes up so you can see why, it simply is not blessed.

The first version of this was wired `WantedBy=boot-complete.target`, which is a
`Wants=` — a failing check left the target `active`, the slot was blessed anyway
and nothing rolled back. The mechanism was decorative, and only a real boot
showed it (`health-check=failed`, `boot-complete=active`,
`mark-good-result=success`, `B -> B -> B`). It is `RequiredBy=` now, and
`test-slot-stability.sh` gained a `health-fail` mode that installs a failing
check and asserts the machine rolls back (`B -> A -> A`).

**Fixed: `test-luks-key-portability.sh` failed intermittently on unrelated
changes.** `losetup -P` scans partitions the moment they appear — before `mkfs`
writes a label — so blkid's cache could hold a label-less entry for the very
device the test had just labelled. `blkid -t LABEL=BOOT` then found nothing,
`ab-luks-key` correctly warned that it could not find the BOOT partition, and
the test recorded two failures that looked like the product was broken when
nothing about it had changed. It is the same trap `test-state-directives.sh`
documents for `blkid -L overlay`.

The cache is now dropped before each invocation, and the harness asserts up
front that the label resolves to the device it just made — so a recurrence is
reported as `HARNESS-FAIL` rather than as a product failure. `ab-luks-key` also
gained a cache-bypassing `blkid -c /dev/null` retry, since a scan before a label
is written is possible on a real machine too.

**Fixed: machines being imaged never appeared on the Imaging page.** A machine
would show `booting imager` on the Provisioning page, image perfectly, boot —
and never show up on the Imaging page at all, with no error anywhere.

The imager posts progress to `<image host>/api/imaging/report`. That is the only
address it has: the iPXE scripts pass `imager.url=` and nothing else, so `init`
derives the report URL from the host serving the image. That host is the
provisioning nginx, whose config had exactly three locations — `/images/jobs/`,
`/`, `/health` — and no `/api/` at all. Every report was a POST into the static
file root, answered 404 and discarded. The web UI runs as a separate stack on
port 8080 and nothing bridged the two.

Reporting is best-effort by design, so `report()` ended in `|| true` and said
nothing. That silence is why this could sit there looking like a UI bug.

- The provisioning nginx now proxies **exactly** `/api/imaging/report` and
  `/api/imaging/checkin` to the web UI, configured by `WEBUI_ADDR` in
  `server/.env` (default `127.0.0.1:8080`, right when both stacks share a host).
- Exact-match locations, not a prefix over `/api/`. The rest of the API is the
  admin surface, and although it is all behind `require_auth`, none of it should
  be reachable from the imaging segment. `/api/imaging` (the list) and
  `/api/imaging/<id>` (delete) are deliberately not routed either.
- A `WEBUI_ADDR` that does not resolve no longer takes PXE down with it: nginx
  refuses to start on an unresolvable `proxy_pass` name, so the entrypoint runs
  `nginx -t`, falls back to the default and says so. Losing the progress display
  is survivable; losing PXE is not.
- The imager now prints a note on the console, once per run, when it cannot
  reach the report URL — imaging still continues, but the failure is no longer
  invisible.

This also fixes first-boot check-in, which used the same derived URL: the
`checkin_url` the imager leaves in `/boot/ab-deploy.json` pointed at the same
dead route, so `ab-checkin` could never reach the server either. Note a machine
that has already moved to its production network still cannot reach
`SERVER_IP`; that is the same limitation `UPDATE_IP` exists for.

`scripts/test-imaging-report-route.sh` runs the real container against a stub
web UI and asserts both that the two endpoints arrive and that nothing else
does. Without the fix it reports `POST /api/imaging/report -> 404`.

**The A/B fallback is now armed for updates, not for every boot.** A slot carries
`<SLOT>_PROVEN` in grubenv; GRUB boots a proven slot outright and only puts an
**unproven** one on probation. `ab-slot-pending.sh` sets `_PROVEN=0` on the slot
an update has just written, and `ab-mark-good` sets it back once that slot boots.

Previously the counter was armed on *every* boot, so every boot for the life of
the machine had to be blessed before the next reboot, and any failure to do so
switched slots. That is the mechanism behind the bug below; this removes it
rather than narrowing it. It is also what Android
(`successful`/`tries_remaining`), ChromeOS (`cgpt successful`/`tries`) and
systemd-boot (`entry+N-M.conf`) do — RAUC's reference GRUB integration, which
this project followed, arms every boot. Between updates nothing writes to `/boot`
at all now.

`ab-slot-pending.sh` is wired as RAUC's `[handlers] post-install`, not into
`ab-update`, so `rauc install <bundle>` typed by hand — which `make-bundle.sh`
prints and the web UI shows — gets the same protection. `ab-update` calls it
again as belt and braces, since a handler that did not run means an update with
no rollback.

The risk in this change is the inverse of the bug: arm too little and rollback
quietly disappears, discoverable only when needed. `test-slot-stability.sh`
gained a `rollback` mode that stages what an update stages, makes the new slot
fail to mark good, and asserts the machine falls back (`B -> A -> A`). Both ends
run nightly.

**Fixed: a machine could switch slots by itself after a perfectly good boot.**
Image a machine, log in, change something, reboot — and it comes up on the other
slot with none of your changes. It reads as the machine reverting itself. The
report that found this was "boot the image, `hostnamectl set-hostname`, reboot,
and it boots slot B".

`grub.cfg` arms a one-shot fallback on every boot (`<SLOT>_TRY=1`) and
`ab-mark-good` disarms it once the system is up. It was ordered
`After=multi-user.target`, and on a measured machine that meant it ran **~92
seconds** into boot while the login prompt appeared at **~38 seconds** — a ~50
second window where the machine was fully usable and the counter was still
armed. Reboot inside it and GRUB does exactly what it was told: skip this slot.
Nothing about the machine looked wrong, because nothing was: the fallback fired
as designed, far too late to be disarmed by a unit nobody was waiting for.

Three fixes:

- **`ab-mark-good` now runs before logins are permitted**
  (`Before=systemd-user-sessions.service`) rather than after
  `multi-user.target`. The rule is now "if you can log in, this slot is already
  marked good". The trade is deliberate and narrow: a boot that reaches a login
  prompt and then fails a later service no longer rolls back on its own.
  Everything A/B rollback actually exists for — an unbootable kernel, a broken
  initramfs, a root that will not mount, a drop to emergency — happens before
  that point and is still covered.
- **An ordering cycle is gone.** `ab-mark-good` was `After=multi-user.target`
  *and* `WantedBy=multi-user.target`, while `ab-checkin` was `After=` it and also
  `WantedBy=multi-user.target`. systemd broke the loop by deleting a job at every
  boot (`Found ordering cycle on multi-user.target/start`). It chose `ab-checkin`
  on the boots that were watched, but both units are only *Wanted*, so both were
  eligible — and deleting `ab-mark-good` means the counter is never reset at all.
  `ab-checkin` no longer orders itself after `ab-mark-good`; it only reports, so
  the edge bought nothing.
- **Failures are no longer silent.** `ab-mark-good.sh` exited 0 on every failure
  path, so `systemctl status ab-mark-good` said "success" on a machine whose
  counter was still armed — the one place anyone would look actively said
  nothing was wrong. It now exits non-zero, says the counter is still armed, and
  reads the value back after writing rather than trusting the write.

`scripts/test-slot-stability.sh` is the regression test, in the nightly boot
matrix: boot a machine three times and assert it never changes slot on its own.
Its `early-reboot` mode reboots 45 seconds in, which is what a person does and
what used to fail. Nothing covered this before — the only related check was a
soft `WARN` in the update test — so a machine that alternated slots for the rest
of its life would have shipped green.

**Bundles can be deleted from the web UI.** There was no way to remove one short
of reaching into `output/bundles/` on the server, so the list only ever grew —
at roughly half a gigabyte per bundle.

The delete is not just an `rm`, because `bundles/latest` exists: `ab-update` with
no arguments fetches that pointer and installs whatever it names, and directory
listing is off on the HTTP server, so it is the only way an unattended machine
finds a bundle at all. Deleting the file it named would have broken every
unattended machine at once, reported as a download failure or `is not a RAUC
bundle` rather than as something missing on the server. Deleting now moves the
pointer to the newest bundle left, or removes it when the last one goes.

The Updates page marks which row is **latest** and how many machines report each
version, so both consequences are visible before the confirm. Deleting a version
the fleet is running is safe — the update is on their disks and rollback uses the
other slot — but it does prevent installing that version anywhere else, which the
confirm says. Deletion is refused while a bundle build is running, since that
build rewrites the same pointer.

**`--slot-private-upper`: each slot can have its own overlay upper layer.** A/B
protected a machine from a bad image but not from a bad change. Both slots share
one upper layer, so a broken `/etc/fstab` or a bad `systemd-networkd` file stops
slot A booting *and* slot B, which reads the same layer — and "boot the other
slot" is the first thing anyone tries.

Built with `--slot-private-upper`, the overlay partition carries `upper-A` and
`upper-B` instead of one `upper`. Nothing written while running A is visible from
B, so the other slot really is a fallback. Not the default: the slots then share
nothing the overlay covers, so pair it with `--persist /home` for what should
survive the crossing. Machine identity (machine-id, SSH host keys) lives outside
the upper layer and stays shared either way.

Like the state model, it cannot be turned on or off by an update — the machine
records `overlay+per-slot-upper` in `/var/lib/overlay/.model` and refuses an
image declaring the other layout, because every write it has made is in the store
the old one used. The recovery *reset writable state* entry sets aside only the
booted slot's layer, and `ab-overlay-diff` reads that slot's layer and stops
claiming the files it lists shadow the image on both slots.

Available as `SLOT_PRIVATE_UPPER=1` to `make image` and as a checkbox under
**Writable state** in the web UI. See
[BUILDER.md](docs/BUILDER.md#a-separate-upper-layer-per-slot).

## v0.5.1 — 2026-08-08

**Two images of the same kind can coexist.** The output name was
`{distro}-{suite}-{arch}-ab` and nothing else, so a second Debian 13 amd64 build
silently replaced the first — and with it the image a deployed machine was made
from (which is what a bundle for that machine has to be built from), its
sidecars, and **the LUKS passphrase**, which the secrets manager files under the
image name. An unrelated later build destroyed the recovery key of a machine
already in the field, discoverable at the earliest by whoever needed it at a
console.

Builds are named now. Left blank, a free name is chosen (`…-ab`, then `-2`,
`-3`), so the default cannot overwrite anything. Give a name and it is honoured,
and refused if taken — with a free alternative named in the refusal, which the
UI offers and fills in. Tick **Replace if it exists** to mean it.

## v0.5.0 — 2026-08-08

**Over-the-air updates work on encrypted machines, and reach machines that have
left the provisioning network.** Every fix here came out of one bundle that
would not install; four separate causes, none of which reported itself
accurately.

### Encrypted updates

Encrypted machines could never be updated. Two independent bugs, both the same
mistake — an image carrying something that belongs to one disk:

- **crypttab named the build's own LUKS UUIDs.** `cryptsetup luksUUID` returns a
  value created by that `luksFormat`, so the crypttab described the builder's
  loopback file. A bundle carries the rootfs *and* the initramfs built from it,
  so a machine given a bundle from another build hunted for three volumes that
  exist nowhere on its disk and waited forever
  (`Waiting for encrypted source device UUID=…`). Volumes are now addressed by
  `PARTLABEL=`, which comes from the GPT and is identical in every build.
- **The LUKS bootstrap keyfile was baked into the image**, and therefore into
  the initramfs a bundle delivers — so an update handed the machine the
  *builder's* key (`No key available with this passphrase`, then an initramfs
  shell). The key now lives on the BOOT partition, which no bundle writes, and
  `scripts/init-premount/ab-luks-key` fetches it at boot.

If **all three** volumes fail to resolve, that is provenance, not a damaged
disk. One failing volume is hardware.

Machines imaged before this need a one-time crypttab repair from the other slot;
the procedure is in [docs/UPDATES.md](docs/UPDATES.md). Re-imaging is simpler
where it is available.

### Security

- **Update bundles no longer contain LUKS key material.** `make-bundle.sh` tars
  the whole root slot with no exclusions, so every bundle built from an
  encrypted image carried that image's keyfile — and bundles are published over
  plain HTTP for any machine to fetch.
- **An image with no signing certificate now ships an empty keyring** rather
  than `ca-certificates.crt`. The old fallback did not merely fail to help: it
  made the machine accept a bundle signed by anything chaining to any public CA.
- **The web UI no longer answers for paths it does not own.** Its SPA catch-all
  returned `index.html` with a 200 for any unknown path, so a bundle URL on the
  UI's port downloaded the React app under a `.raucb` name.

### Updates reach the fleet

- **`UPDATE_IP`** (optional, in `server/.env`) publishes `/bundles/` on a second
  address. The imaging listener is bound to `SERVER_IP` alone, but a machine is
  only on that segment while it is being imaged — afterwards it lives on the
  LAN, which is when it needs updates. `/images/`, `/imager/` and `/hosts/` stay
  where they were.
- **`build-image.sh` generates the signing key** if it does not exist, so an
  image and the bundles for it always agree. Previously an image built before
  the first bundle trusted nothing, and failed months later with
  `Verify error: self-signed certificate` — unrepairable remotely, because the
  update is the thing it will not accept.
- **`ab-update` checks the first four bytes are `hsqs`** before handing a URL to
  RAUC, so a wrong URL says so instead of surfacing as a corrupt bundle.
- Hand-set values in `server/.env` survive the Provisioning page saving.

### Streaming

`rauc install <url>` streams over NBD and had been assumed broken. It is not:
the only thing testing it served bundles with `busybox httpd`, which answers a
range wholly past EOF with `200` and the entire file where nginx answers `416`.
RAUC's NBD backend requires `206` with exactly the requested byte count. The
nightly now serves from nginx, and installs report `install-route: streamed`.
The download fallback stays — upstream streaming genuinely dies on flaky
networks — but it is a fallback again rather than the path every update takes.

### Tests

Both encrypted bugs were found by imaging a real machine, because the only thing
watching encrypted updates was a nightly that did not test them. Now:

- `test-luks-key-portability.sh` — real GPT, loop device and LUKS, the real
  init-premount script and hook, in the **fast** job. The volume it opens has no
  passphrase slot, so only the fetched key can open it.
- `test-cross-build-update.sh` — takes the initramfs out of a bundle, runs it
  against a *different* build's disk, and checks it unlocks. Asserts the two
  builds do not share a key, so it cannot pass vacuously.
- `test-image-portability.sh` — asserts a built image carries no LUKS UUID, no
  key material, and an initramfs that fetches rather than carries a key.
- The nightly update test runs twice, unencrypted and encrypted across two
  independent builds, and fails on `Waiting for encrypted source device` by name.
- Every built image's keyring fingerprint must match the signing certificate.

### Other

- Image sidecars record `update_keyring_sha256`.
- `builder/run.sh` warns when a build will not fit on the host. Docker Desktop's
  disk image is sparse and provisioned far above what the host can supply, so
  `df` inside the builder reports space that does not exist and overrunning
  corrupts Docker rather than failing with ENOSPC.

## v0.4.0 and earlier

See the git history; this file starts at v0.5.0.
