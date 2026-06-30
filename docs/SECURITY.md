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

The current image does not enable LUKS by default. If you require encryption at
rest, add `cryptsetup`-based setup in `builder/overlay` and an initramfs unlock
mechanism appropriate to your threat model (TPM-bound keys are preferable to
keys stored on an unencrypted `/boot`).

## Reporting a vulnerability

Report security issues privately to the maintainer with reproduction steps and the
affected commit.
