# Web Management UI

A browser-based control panel that ties the whole system together — build images,
manage the image library, configure and run the provisioning server, and **watch
machines get imaged live**. It orchestrates the builder/imager/server containers
through the Docker socket.

## Features

- **Build wizard** — pick distribution (Debian or Ubuntu), release, hostname,
  user, sizes, compression, and extra packages, then start a build and watch a
  **progress bar and live log** in the browser (with cancel). The bar tracks 14
  named phases reported by the builder, so a long step like debootstrap or
  compression is identifiable rather than just slow. Navigating away and back
  reattaches to the running build.
- **One-click imager build**, for the architecture selected in the build form.
  The imager is a kernel the target machine executes, so an amd64 imager cannot
  netboot an arm64 machine; the page says so when the one you need is missing.
- **Image library** — list, download, and delete built images, with distro/
  release/encryption metadata and SHA256 for each, and a **Deploy** button that
  points the provisioning server at an image.
- **Image files** — create, upload, edit, move and delete the files copied into
  every image, from the browser. See below.
- **Job history** — past builds and their full logs survive UI restarts
  (persisted under `output/jobs/`).
- **Turnkey provisioning** — the UI lists the host's network interfaces; pick the
  one facing the machines and the server IP, subnet and DHCP lease range are
  derived from it. DHCP and TFTP are bound to that interface alone, so the
  imaging network is self-contained and the host's other networks never see it.
  A readiness check blocks Start until the imager is built and an image chosen.
- **Per-machine images** — assign specific machines a specific image by MAC,
  with an optional label and its own post-imaging action. Everything else on the
  switch gets the default image. Machines already seen by the monitor have an
  *assign image…* link, so you can plug in a fleet and target from what shows up
  rather than collecting MAC addresses first.
- **Imaging** — a live view of machines writing an image right now, with per-machine
  progress reported by the imager itself. A machine that finishes drops off shortly
  after; one that stops reporting is marked stalled and then removed, so the page
  only ever shows current work. The Provisioning page's list is the narrower
  question of who is on the network and still needs an image. If machines show
  `booting imager` there but never reach this page, the imager's reports are not
  arriving — see `WEBUI_ADDR` in [DEPLOYMENT.md](DEPLOYMENT.md).
- **Fleet** — every machine this server has imaged, kept on disk and never expired.
  Machines report once when imaging finishes and again when they boot the image, so
  a machine that imaged and then failed to boot shows as **never-booted** rather
  than being indistinguishable from a success.
- **Updates** — build a signed RAUC bundle from an image you have already built,
  and see which versions the fleet is running. Installing a bundle writes the slot
  a machine is not running on and reboots into it, with automatic rollback if that
  slot fails to come up. Bundles can be deleted here too; the one marked **latest**
  is what a machine running plain `ab-update` installs, and deleting it moves that
  pointer rather than leaving the fleet fetching a file that is gone. See
  [UPDATES.md](UPDATES.md).
- **Secrets manager** *(optional)* — connect OpenBao or HashiCorp Vault and have
  encrypted builds generate their own LUKS recovery passphrase and file it under
  the image's name, instead of somebody inventing one and keeping it in a note.
  See below.
- **Disk usage** at a glance on the dashboard.

## Image files

`overlay.d/` holds your own files — the ones copied over the image's root
filesystem, keeping their paths, so `/etc/hosts` here is `/etc/hosts` on every
machine imaged from it. It is a directory in the repository, and it is also
editable from the **Image Files** page: create a file and type its contents,
upload one, change its mode, move it, or remove it.

The same manager opens in a dialog from the Build page's *Customize the
filesystem* panel, so files can be added mid-build without losing the form.

Worth knowing:

- **The mode is part of the file.** It is preserved into the image, so a script
  shipped `0644` is a script that does not run on the machine — nothing warns
  you, it simply sits there. The list has a one-click 0644/0755 toggle, and the
  editor flags a path that looks like a program but is not executable.
- **Deleting the last file in a directory removes the directory too.** An empty
  `overlay.d/etc/netplan` would otherwise be copied into every image as an empty
  `/etc/netplan`, which for netplan means a machine that boots with no network
  configuration at all.
- **Binary files are fine** — upload them and they are copied verbatim. They
  just cannot be edited in the browser; download them instead.
- Files over 1 MiB are shipped but not editable inline. Uploads are capped at
  256 MiB.
- Nothing here is committed: the directory is gitignored except its README.

If the repository is mounted read-only into the UI container, the page says so
and falls back to showing the host path — the files are the same directory
either way.

## Secrets manager

An encrypted image needs a LUKS passphrase, and for every unlock method except
`passphrase` it is *only* a recovery key — machines unlock from the TPM, from
Tang, or from a keyfile, so nothing types it again until a TPM is cleared by a
firmware update and a machine stops at the initramfs prompt.

Under **Secrets Manager**, point the UI at an OpenBao or HashiCorp Vault KV v2
mount (token or AppRole; namespace, private CA and mount point are all
configurable). Then, on an encrypted build, tick *Generate a random passphrase
and store it* — on by default for `tpm2`, `tang` and `keyfile`, off for
`passphrase`, which somebody has to type at every boot.

What happens then:

- The backend generates 256 bits of randomness and **writes it to the store
  before the build starts**. If the store will not take it, no image is built —
  the reverse order can leave an encrypted image whose recovery key was never
  persisted, and that is not recoverable.
- The passphrase reaches the builder in `LUKS_PASS`, as a typed one does. **The
  builder container never talks to the store**, so nothing privileged gains
  network access or a store credential.
- Building an **update bundle** from that image reads the passphrase back
  automatically instead of prompting for it.
- Encrypted images with a stored passphrase show a key icon in the image library;
  clicking it reveals the passphrase, because the moment you need it is a machine
  stopped at an initramfs prompt.

Two things to get right:

- **The credential needs write access**, not just read. A read-only policy passes
  the connection test and fails the first build.
- **Rebuilding the same image name files a new passphrase.** KV v2 keeps the old
  version and machines already imaged still need it — which is why a KV v1 mount
  is rejected.

The store token can be set as `BAO_TOKEN` (or `VAULT_TOKEN`) in `webui/.env`
instead of through the UI; it then takes precedence and never lands in the app's
own config file. Everything saved through the UI goes to
`output/.secrets-store.json`, mode 0600.

The same thing is available on the command line via `scripts/luks-secret.sh` —
see [BUILDER.md](BUILDER.md#storing-the-passphrase-in-a-secrets-manager).

## Running it

```bash
cp webui/.env.example webui/.env
# Edit it:
#   ADMIN_PASSWORD — UI login password
#   SECRET_KEY     — random string
make webui
```

Open **http://localhost:8080** and log in with `ADMIN_PASSWORD`.

No path configuration is needed. Compose mounts the repository root at `/project`
in the UI container, and the UI asks the Docker daemon where that mount came from
on the host — which is what the builder/imager containers need for their own bind
mounts. `HOST_PROJECT_DIR` in `webui/.env` only overrides that detection; set it
if you run `docker compose` from outside the `webui/` directory, and then it must
be the absolute host path of *this* checkout. A stale value mounts an empty
directory and builds fail with `unable to prepare context: path
"/project/builder" not found` — so the Dashboard and Build pages check this on
load and show what to fix.

## How it works

```
browser ─▶ webui (FastAPI + React)
              │  reads ./output, writes server/.env
              └─ docker socket ─▶ builder / imager / server containers
                                   (live logs streamed back via SSE)
```

- The backend launches `docker build` + `docker run` for the builder/imager and
  `docker compose` for the provisioning server, streaming combined output to the
  browser over Server-Sent Events.
- Authentication is a single admin password (JWT). Run the UI only on a trusted
  network — it has full control of the Docker host.
- FastAPI's interactive API documentation is live at `/docs` (Swagger UI), with
  `/redoc` and the raw schema at `/openapi.json`. The schema is readable without
  logging in; every endpoint it describes requires auth.

## Developing the UI

The backend runs directly with uvicorn — no Docker needed for API work. It
wants the same environment the container gets, plus a `PROJECT_DIR` pointing at
a scratch directory (it defaults to `/project`, the container mount, which does
not exist on your machine):

```bash
cd webui/backend
pip install -r requirements.txt
PROJECT_DIR=$(mktemp -d) ADMIN_PASSWORD=dev SECRET_KEY=dev-secret \
  uvicorn app.main:app --reload --port 8080
```

For the frontend, `npm run dev` in `webui/frontend` starts Vite with a proxy
that forwards `/api` to the backend, so both halves reload live:

```bash
cd webui/frontend
npm ci
npm run dev
```

## Security note

The UI container mounts the Docker socket, which is equivalent to root on the
host. Restrict access to the UI (strong `ADMIN_PASSWORD`, trusted network only,
ideally behind a TLS reverse proxy).

The smallest working TLS front is [Caddy](https://caddyserver.com/), which
obtains and renews the certificate itself. A complete `Caddyfile`:

```
webui.example.com {
    reverse_proxy localhost:8080
}
```

If the host has several networks, also bind the compose port to the management
interface rather than every address — in `webui/docker-compose.yml`, publish
`<management IP>:8080:8080` instead of `8080:8080` — so the UI (and the login
form in front of the Docker socket) is not reachable from the imaging segment
or anywhere else it has no business being.
