# Deployment & Operations

## Provisioning server setup

The intended path is the web UI: open **Provisioning**, pick the interface facing
the machines, pick the image, **Save**, **Start**. Everything else — server IP,
subnet, lease range — is derived from the interface, and the UI refuses to start
until the imager is built and an image is selected.

To drive it from the command line instead:

```bash
cd server
cp .env.example .env
$EDITOR .env          # set SERVER_IP, INTERFACE, IMAGE_FILE
docker compose up -d --build
docker compose logs -f
```

The stack uses **host networking** so DHCP and TFTP reach the imaging segment —
run it on a Linux host attached to that switch. `INTERFACE` is mandatory: dnsmasq
binds to it alone (`bind-interfaces`), and nginx listens only on `SERVER_IP`, so
neither service is reachable from the host's other networks. Starting without
`INTERFACE` is refused rather than defaulting to every NIC.

### `.env` reference

| Variable | Mode | Description |
|----------|------|-------------|
| `SERVER_IP` | both | This server's IP on the imaging network (required) |
| `SERVER_PREFIXLEN` | both | Prefix length for `SERVER_IP`. When set, the server assigns that address to `INTERFACE` at startup if absent (runtime only) |
| `INTERFACE` | both | NIC to serve on — **required**; DHCP/TFTP are confined to it |
| `IMAGE_FILE` | both | Image filename in `./output` to deploy (e.g. `debian-trixie-ab.img.zst`) |
| `ACTION` | both | After imaging: `reboot` \| `poweroff` \| `shell` |
| `MODE` | both | `dhcp` (standalone, default) or `proxy` (coexist) |
| `DHCP_RANGE_START` / `_END` | dhcp | Lease range |
| `DHCP_NETMASK` / `DHCP_ROUTER` / `DHCP_DNS` / `LEASE_TIME` | dhcp | Standalone DHCP options |
| `PROXY_SUBNET` | proxy | Network address of the LAN, e.g. `192.168.1.0` |
| `WEBUI_ADDR` | both | Where the web UI's API is, so machines being imaged can report progress into it. Default `127.0.0.1:8080`. See below |

### Progress reporting (`WEBUI_ADDR`)

The imager posts progress to `<image host>/api/imaging/report` — it is given no
other address, since the iPXE scripts pass only `imager.url=`. That host is this
server, so nginx forwards exactly `/api/imaging/report` and
`/api/imaging/checkin` to the web UI and nothing else; the rest of the API is
the admin surface and has no business on the imaging segment.

`WEBUI_ADDR` defaults to `127.0.0.1:8080`, which is right whenever the web UI
runs on this host. Set it if the UI is elsewhere. A hostname works if it
resolves — if it does not, nginx would refuse to start and take PXE with it, so
the entrypoint checks and falls back to the default with a note in the log.

**Symptom if this is wrong:** machines image perfectly and never appear on the
Imaging page. The imager prints a note on the console once per run when it
cannot reach the URL; otherwise the failure is silent by design, because
reporting must never hold up an imaging run.

### standalone DHCP vs proxyDHCP

- **standalone DHCP (`MODE=dhcp`, default)** — the self-contained option. This
  server owns the provisioning segment and assigns IPs *and* boot info, so
  nothing else has to exist on it. Needs a dedicated NIC, switch, or VLAN.
  **Do not** point it at a LAN that already has a DHCP server.
- **proxyDHCP (`MODE=proxy`)** — your existing router/DHCP keeps assigning IPs;
  this server only answers the PXE "where do I boot?" question, so there is no
  conflict. Requires `PROXY_SUBNET`. Use it when the machines cannot be moved
  onto a segment of their own.

Everything a machine needs is served by this stack — the iPXE bootloaders are
baked into the dnsmasq image, and the imager and disk image come from `output/`
over HTTP. Targets never need internet access.

## Imaging many machines at once

The intended topology is a second NIC on the server going to a dumb switch, with
the target machines on that switch and nothing else:

```
   server ─ eth0 ── your LAN (web UI, internet)
          └ eth1 ── dumb switch ─┬─ machine 1
                                 ├─ machine 2
                                 └─ machine N
```

Pick `eth1` as the provisioning interface. DHCP and TFTP bind to it alone, so the
switch is a self-contained imaging network and your LAN never sees the DHCP
server.

**The provisioning NIC does not need an IP address beforehand.** It normally
won't have one — nothing on that segment hands out addresses, because this server
is what will. Such a NIC is listed as *no IP address* (and sorted first, since on
a turnkey setup it is the one you want). Selecting it fills in a free subnet —
`192.168.50.0/24` unless that collides with something the host already has — and
the provisioning server assigns the address to the NIC and brings the link up
when it starts.

That assignment is made at **runtime only**: a reboot reverts it and starting the
server re-applies it, so the host's permanent network configuration is never
touched. If you would rather set it yourself, configure the NIC on the host and
it will appear with its address already in place; the server leaves an existing
address alone.

Machines image **concurrently** — each gets a lease, pulls the ~100 KB iPXE
binary over TFTP, then downloads the image over HTTP, which is where the bulk of
the transfer happens. The practical ceiling is the switch's bandwidth and the
server's disk, not the software.

The default lease range covers ~100 machines; widen it under **Advanced** if you
need more.

## Targeting specific machines

By default every machine that boots gets the image chosen under *Image to
deploy*. To send particular machines a different image, add them under
**Per-machine images** on the Provisioning page — by MAC, with an optional label
and its own post-imaging action.

Machines that have already PXE-booted appear in the live monitor with an
**assign image…** link, so you can plug in the fleet, see what turned up, and
assign from there instead of collecting MAC addresses by hand.

Mechanically, DHCP points every client at `boot.ipxe`, which chains to
`/hosts/<mac>.ipxe` and falls back to `/default.ipxe` when no such file exists:

```
chain --autofree http://SERVER/hosts/${mac:hexhyp}.ipxe || chain --autofree http://SERVER/default.ipxe
```

The web UI generates those per-host scripts from `output/hosts/assignments.json`
and regenerates them whenever the server IP or an assignment changes. Nothing is
configured on the machines themselves.

### Naming machines

An image cannot carry a hostname: every machine written from it would answer to
the same one. `--hostname` at build time sets the *image's* name, which is the
right default for a fleet of interchangeable machines and wrong the moment you
need to tell them apart.

Set a **Hostname** on the assignment instead. It travels the same path as the
image does — assignment → the machine's `/hosts/<mac>.ipxe` → `imager.hostname=`
on the kernel command line → `/boot/ab-deploy.json` → `machine-identity.service`
on first boot — so nothing is configured on the machine itself and nobody has to
log in afterwards to type it.

It is stored alongside the machine-id and SSH host keys on the overlay
partition, not merely written to `/etc/hostname`. That matters twice: `/etc` is
part of the root, so an image could later ship a file that shadows it, and on an
image built with `--slot-private-upper` a name set while running slot A would
not exist in slot B. Re-applied from that store on every boot, it is correct in
both slots and survives updates. `/etc/hosts` gets the matching `127.0.1.1`
entry, without which `sudo` waits on a name-resolution timeout.

Leave it blank and the machine keeps whatever the image was built with.

Names are validated, not corrected: `web 01`, `-web01` and `web_01` are refused
rather than quietly reshaped, because a name in the UI that differs from the
name on the machine is discovered by whoever cannot resolve it. Two machines
cannot be given the same name, case-insensitively.

Re-imaging rewrites the whole disk, overlay included, so a machine adopts
whatever name its assignment carries at that point — re-imaging is how you
change what a machine is.

## Imaging machines

1. Build the image and imager (`make image`, `make imager`).
2. Start the server.
3. On each target machine, enable **network/PXE boot** (BIOS: enable PXE; UEFI:
   enable network boot and **disable Secure Boot** — both the iPXE netboot
   binary and the installed image's GRUB are unsigned). The imaged system boots
   on both BIOS and UEFI firmware, so mixed fleets are fine.
4. Power them on. Each PXE-boots, runs the imager, writes the disk, and reboots
   into Debian A/B. Watch `docker compose logs -f`.

### Imager command-line options

The imager reads these from the kernel command line. `boot.ipxe` only dispatches
on MAC; the kernel line lives in `server/http/default.ipxe.tmpl` (the fallback)
and in the per-machine scripts the web UI generates under `output/hosts/`:

| Option | Default | Meaning |
|--------|---------|---------|
| `imager.url=` | (required) | HTTP URL of the image |
| `imager.disk=` | largest non-removable | Target disk, e.g. `/dev/nvme0n1` |
| `imager.compress=` | `auto` | `auto` \| `zstd` \| `gzip` \| `none` |
| `imager.action=` | `reboot` | `reboot` \| `poweroff` \| `shell` |
| `imager.wipe=` | `0` | `1` wipes the partition table first |

## Testing without hardware (QEMU)

You can validate the whole flow locally. The imager and a built image are all you
need:

```bash
# Serve ./output over HTTP, then netboot the imager against a blank disk:
python3 -m http.server 8000 --directory output &
truncate -s 8G /tmp/target.img
qemu-system-x86_64 -m 1536 \
  -kernel output/imager/vmlinuz -initrd output/imager/initramfs.img \
  -append "imager.url=http://10.0.2.2:8000/debian-trixie-ab.img imager.compress=none imager.action=poweroff console=ttyS0,115200" \
  -drive file=/tmp/target.img,format=raw,if=virtio \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -nographic -serial mon:stdio -no-reboot

# Then boot the freshly imaged disk:
qemu-system-x86_64 -m 1024 -drive file=/tmp/target.img,format=raw,if=virtio -nographic -serial mon:stdio
```

To test the full PXE chain in QEMU, boot a VM with `-boot n` on a network where the
provisioning server is running.

## Updating the served image

Rebuild (`make image`), drop the new file in `./output`, update `IMAGE_FILE` in
`.env`, and `docker compose up -d` to re-render `default.ipxe`. (Setting it from
the web UI does this for you, and also regenerates any per-machine scripts.) No rebuild of the
containers is required for a new image — only when `IMAGE_FILE` changes.
