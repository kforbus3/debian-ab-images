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
- The builder publishes a `<image>.sha256` next to each image and the imager
  verifies the download against it before rebooting, so a truncated or corrupted
  transfer fails loudly instead of producing a broken machine. (This is an
  integrity check against accidents, not an authenticity check — HTTP is
  unauthenticated; control the network.)
- The web UI passes build secrets (login password, LUKS passphrase) to the
  builder via the process environment, not command-line arguments, so they don't
  appear in `ps` or persisted job records. They remain visible to anyone with
  Docker access (`docker inspect` on a running build) — which is root-equivalent
  anyway.
- Web UI sessions are JWTs; live log streams use short-lived (60 s) per-job
  tokens in the query string instead of the session token. Failed logins are
  rate-limited.
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

### Where the recovery passphrase lives

"Store it safely" is the part that fails in practice. For every unlock method
except `passphrase`, the passphrase is never typed again during normal
operation — machines unlock from the TPM, from Tang, or from a keyfile — so
nothing exercises it until the day a TPM is cleared by a firmware update and a
machine stops at the initramfs prompt.

The web UI can therefore generate it and put it in a secrets manager instead
(OpenBao or HashiCorp Vault, KV v2). Configure a store under **Secrets Manager**,
then tick *Generate a random passphrase and store it* on an encrypted build:

- The passphrase is 256 bits of randomness, generated in the backend — never
  typed, never displayed during the build, never on a command line.
- It is written to the store **before the build starts**. If the store will not
  take it, no image is built. The reverse order can produce an encrypted image
  whose recovery key was never persisted, which is not recoverable; a leftover
  secret from a failed build is merely untidy.
- It is filed under the image's name (`<mount>/<prefix>/<image>.img`), with the
  distro, suite, arch and unlock method alongside it.
- The **builder container never talks to the store.** It receives the passphrase
  in `LUKS_PASS` exactly as it does when one is typed, so nothing privileged
  gains network access or a store credential.
- Packaging an update bundle from an encrypted image reads the passphrase back
  automatically rather than prompting for it.

Two things to be deliberate about:

- **The store credential needs write access**, not just read. A read-only policy
  passes the connection test and fails the first build.
- **Rebuilding the same image name files a new passphrase.** KV v2 keeps the
  previous version, and machines already imaged still need it — so keep version
  history on that mount (a KV v1 mount is rejected for this reason).

Revealing a stored passphrase from the UI is deliberately one click: the moment
you need it is a machine stopped at an initramfs prompt. It is no wider than the
session already is — this UI drives the Docker socket, which is root on the host.

On the command line, `scripts/luks-secret.sh` does the same job against the
`bao`/`vault` CLI and writes the same payload, so an image built either way is
recoverable from the other. See
[BUILDER.md](BUILDER.md#storing-the-passphrase-in-a-secrets-manager).

## Reporting a vulnerability

Report security issues privately to the maintainer with reproduction steps and the
affected commit.
