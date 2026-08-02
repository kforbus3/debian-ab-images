# Web Management UI

A browser-based control panel that ties the whole system together — build images,
manage the image library, configure and run the provisioning server, and **watch
machines get imaged live**. It orchestrates the builder/imager/server containers
through the Docker socket.

## Features

- **Build wizard** — pick distribution (Debian or Ubuntu), release, hostname,
  user, sizes, compression, and extra packages, then start a build and watch its
  **log stream live** in the browser (with cancel).
- **One-click imager build.**
- **Image library** — list, download, and delete built images, with distro/
  release/encryption metadata and SHA256 for each, and a **Deploy** button that
  points the provisioning server at an image.
- **Job history** — past builds and their full logs survive UI restarts
  (persisted under `output/jobs/`).
- **Turnkey provisioning** — the UI lists the host's network interfaces; pick the
  one facing the machines and the server IP, subnet and DHCP lease range are
  derived from it. DHCP and TFTP are bound to that interface alone, so the
  imaging network is self-contained and the host's other networks never see it.
  A readiness check blocks Start until the imager is built and an image chosen.
- **Live imaging monitor** — machines that are PXE-booting / being imaged,
  merged from the dnsmasq **and** nginx logs, so a machine shows **imaged** once
  it has fully downloaded the image.
- **Disk usage** at a glance on the dashboard.

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

## Security note

The UI container mounts the Docker socket, which is equivalent to root on the
host. Restrict access to the UI (strong `ADMIN_PASSWORD`, trusted network only,
ideally behind a TLS reverse proxy).
