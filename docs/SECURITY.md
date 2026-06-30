# Security

## Imaging is destructive — control the network

The provisioning server will re-image **any** machine that PXE-boots from it. Run
it only on a network (or isolated switch) where every PXE-booting machine is meant
to be wiped and re-imaged. Prefer `MODE=proxy` on shared LANs (it only answers PXE
requests) and restrict it to one interface with `INTERFACE=`.

## Image credentials

- The image is built with a username/password you pass in. **Use a strong
  password** and change it after first boot, or use `--ssh-pubkey` for key-only
  access and a throwaway password.
- `root` is locked; administration is via the sudo user.
- Don't commit images — they contain the password hash. `.gitignore` excludes
  `output/` and `*.img*`.

## RAUC signing keys

- Update bundles are GPG/x509-signed. **Keep the CA and signing private keys
  off the device and out of git.** Only the CA *certificate* (`keyring.pem`) ships
  in the image.
- `.gitignore` excludes `*.pem`, `*.key`, `*.crt`, and `certs/`.

## Network transport

- PXE/TFTP and the image are served over plain HTTP on the local segment — fine
  for a trusted provisioning LAN. Do not expose the provisioning server to
  untrusted networks.
- For UEFI Secure Boot targets you must sign the iPXE binary / use a signed
  shim chain; by default, disable Secure Boot on the targets during imaging.

## Disk encryption

Optional LUKS2 encryption (`--encrypt`) covers both root slots and the overlay
(`/boot` stays plaintext for GRUB). Choose an unlock method by threat model
(`--unlock`):

- **`tpm2`** — key sealed to the machine's TPM; never on disk. Best where a TPM
  exists.
- **`tang`** — key fetched from a Tang server on a trusted LAN (NBDE); never on
  disk. Best no-TPM auto-unlock.
- **`keyfile`** — key embedded in the initramfs on the same disk. Convenient and
  universal, but provides **weak at-rest protection** (pulling the disk yields
  the key). Prefer `tpm2`/`tang` for real protection.
- **`passphrase`** — prompt at boot; most secure, not unattended.

For `tpm2`/`tang`, a bootstrap keyfile makes the first boot unattended, then a
first-boot service enrolls the TPM/Tang and **destroys the keyfile**, leaving no
key on disk. The `--luks-passphrase` you supply is always kept as a recovery key —
store it safely. See [BUILDER.md](BUILDER.md#disk-encryption-luks2).

## Reporting a vulnerability

Report security issues privately to the maintainer with reproduction steps and the
affected commit.
