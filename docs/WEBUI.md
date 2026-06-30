# Web Management UI

A browser-based control panel that ties the whole system together — build images,
manage the image library, configure and run the provisioning server, and **watch
machines get imaged live**. It orchestrates the builder/imager/server containers
through the Docker socket.

## Features

- **Build wizard** — pick suite, hostname, user, sizes, compression, and extra
  packages, then start a build and watch its **log stream live** in the browser.
- **One-click imager build.**
- **Image library** — list, download, and delete built images.
- **Provisioning control** — edit the server config (proxyDHCP/DHCP, server IP,
  image to deploy, post-image action), start/stop the PXE server.
- **Live imaging monitor** — see machines that are PXE-booting / being imaged,
  parsed from the dnsmasq logs, refreshed automatically.

## Running it

```bash
cd webui
cp .env.example .env
# Edit .env:
#   ADMIN_PASSWORD   — UI login password
#   SECRET_KEY       — random string
#   HOST_PROJECT_DIR — ABSOLUTE host path to this repository
docker compose up -d --build
```

Open **http://localhost:8080** and log in with `ADMIN_PASSWORD`.

> `HOST_PROJECT_DIR` is required: the UI runs sibling containers via the Docker
> socket, and bind-mount paths must be resolvable by the Docker daemon on the
> host. Set it to the absolute path of this checkout (e.g. `/opt/debian-ab-images`).

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
