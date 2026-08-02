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
| `INTERFACE` | both | NIC to serve on — **required**; DHCP/TFTP are confined to it |
| `IMAGE_FILE` | both | Image filename in `./output` to deploy (e.g. `debian-trixie-ab.img.zst`) |
| `ACTION` | both | After imaging: `reboot` \| `poweroff` \| `shell` |
| `MODE` | both | `dhcp` (standalone, default) or `proxy` (coexist) |
| `DHCP_RANGE_START` / `_END` | dhcp | Lease range |
| `DHCP_NETMASK` / `DHCP_ROUTER` / `DHCP_DNS` / `LEASE_TIME` | dhcp | Standalone DHCP options |
| `PROXY_SUBNET` | proxy | Network address of the LAN, e.g. `192.168.1.0` |

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

The imager reads these from the kernel command line (set in `boot.ipxe`, rendered
from `.env`). To customize, edit `server/http/boot.ipxe.tmpl`:

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
`.env`, and `docker compose up -d` to re-render `boot.ipxe`. No rebuild of the
containers is required for a new image — only when `IMAGE_FILE` changes.
