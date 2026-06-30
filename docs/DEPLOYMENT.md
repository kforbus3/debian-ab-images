# Deployment & Operations

## Provisioning server setup

```bash
cd server
cp .env.example .env
$EDITOR .env          # set SERVER_IP, IMAGE_FILE, MODE
docker compose up -d --build
docker compose logs -f
```

The stack uses **host networking** so DHCP/proxyDHCP and TFTP reach the LAN — run
it on a Linux host attached to the imaging switch.

### `.env` reference

| Variable | Mode | Description |
|----------|------|-------------|
| `SERVER_IP` | both | This server's IP on the imaging LAN (required) |
| `IMAGE_FILE` | both | Image filename in `./output` to deploy (e.g. `debian-trixie-ab.img.zst`) |
| `ACTION` | both | After imaging: `reboot` \| `poweroff` \| `shell` |
| `MODE` | both | `proxy` (coexist) or `dhcp` (standalone) |
| `INTERFACE` | both | NIC to serve on (recommended; blank = all) |
| `PROXY_SUBNET` | proxy | Network address of the LAN, e.g. `192.168.1.0` |
| `DHCP_RANGE_START` / `_END` | dhcp | Lease range |
| `DHCP_NETMASK` / `DHCP_ROUTER` / `DHCP_DNS` / `LEASE_TIME` | dhcp | Standalone DHCP options |

### proxyDHCP vs standalone DHCP

- **proxyDHCP (`MODE=proxy`)** — your existing router/DHCP keeps assigning IPs;
  this server only answers the PXE "where do I boot?" question. No conflict.
  Requires `PROXY_SUBNET`.
- **standalone DHCP (`MODE=dhcp`)** — for an isolated provisioning switch with no
  other DHCP server. This server assigns IPs *and* boot info. Set the
  `DHCP_RANGE_*` values. **Do not** enable this on a LAN that already has DHCP.

## Imaging machines

1. Build the image and imager (`make image`, `make imager`).
2. Start the server.
3. On each target machine, enable **network/PXE boot** (BIOS: enable PXE; UEFI:
   enable network boot, and disable Secure Boot unless you sign iPXE).
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
