# Changelog

Notable changes per release. Dates are the tag date.

## v1.2.0 — 2026-08-08

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

## v1.1.0 and earlier

See the git history; this file starts at v1.2.0.
