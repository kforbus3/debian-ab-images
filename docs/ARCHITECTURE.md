# Architecture

Three independent, composable pieces: **build** an image, **build** the netboot
imager, and **serve** them to machines over the network.

## 1. Builder (`builder/`)

A privileged Docker container that produces a bootable Debian or Ubuntu A/B disk
image with `debootstrap`.

Flow (`build-image.sh`):
1. Create a sparse image file (sized to its contents by default) and partition
   it (GPT) with a BIOS-GRUB partition, an EFI system partition, a shared
   `BOOT` partition, two root slots (`rootfs-a`, `rootfs-b`), and an `overlay`
   partition. GRUB is installed for both BIOS (`i386-pc`) and UEFI
   (`x86_64-efi`, removable path), so the image boots on either firmware.
2. `debootstrap` a minimal Debian into slot A; install the kernel, GRUB, SSH,
   sudo, RAUC, and growpart; create the login user.
3. Apply the overlay files (RAUC config, GRUB A/B `grub.cfg`, first-boot expand
   service).
4. Install GRUB to the disk; write `grub.cfg` (referencing the exact kernel) and
   a fresh `grubenv`.
5. `rsync` slot A → slot B so both roots are bootable.
6. Optionally compress the image (zstd/gzip).

### Disk layout & boot

The `BOOT` **partition** is shared, but the kernel is not: each slot has its own
`vmlinuz` and `initrd.img` under `/A/` and `/B/`, so an update replaces the
inactive slot's kernel without touching the running one and a rollback gets the
kernel it was built against. `ab-sync-boot.sh` maintains those copies (`--slot both` when a change affects
both slots, such as LUKS enrollment);
`ab-kernel-hook.sh` says plainly that an apt-installed kernel will not be booted.

GRUB's prefix is on the `BOOT` partition; `grub.cfg` selects a slot from `ORDER`,
per-slot `TRY` counters and `_OK` flags in `grubenv`, then boots
`root=LABEL=rootfs-a|b`. This mirrors RAUC's documented GRUB integration, so RAUC
can flip slots by editing `grubenv`.

### Writable state

A/B replaces the root filesystem, so everything a machine writes has to live
somewhere that survives that — the `overlay` partition. **How much of the root
that covers is a property of the image**, declared in a manifest at
`/usr/lib/ab/state.conf` and applied by the initramfs
(`scripts/local-bottom/ab-overlay`) before the switch to root.

Five directives. `overlay`, `persist`, `slot-private` and `volatile` say where a
path's writes go; `reset-on-update` and `keep` say what a slot change does to
them:

| directive | mounted as | backing store | shared between slots? |
| --- | --- | --- | --- |
| `overlay PATH` | overlayfs, lower = the slot's copy | `upper/PATH` | yes |
| `persist PATH` | bind | `persist/PATH` | yes |
| `slot-private PATH` | bind | `slots/<A\|B>/PATH` | no |
| `volatile PATH [SIZE]` | tmpfs | — | no, and not across reboots |

One manifest-wide line changes the first row:

| line | effect |
| --- | --- |
| `upper per-slot` | every `overlay` directive is backed by `upper-<A\|B>/PATH` instead, so **no** overlaid path is shared |

That is `--slot-private-upper`. It exists because A/B otherwise protects a
machine from a bad image but not from a bad change: a shared upper layer means
an edit that stops slot A booting is read by slot B too, so booting the other
slot is no help. Separating the uppers makes the other slot a genuine fallback,
at the cost of the two slots sharing nothing the overlay covers — which is why
it is opt-in and why `persist` is the documented way to keep `/home` shared
anyway. See
[BUILDER.md](BUILDER.md#a-separate-upper-layer-per-slot).

`--state-model` picks the starting manifest; the per-path flags append to it.
See [BUILDER.md](BUILDER.md#writable-state) for what each model contains and
when to choose it.

Two rules the engine enforces, both learned the hard way:

- **Clearing a path means something different per store.** Under an overlay,
  deleting the upper copy uncovers the image's. Under a bind there is nothing
  underneath, so the same delete would destroy the file rather than revert it —
  bind stores are re-seeded from the image after clearing.
- **A layout change cannot ride an update.** The machine records its model in
  `/var/lib/overlay/.model`. An image declaring a different one is refused at
  boot and the slot is booted untouched, because applying it would leave every
  existing file on the partition but in the wrong store — indistinguishable,
  from inside, from having been wiped. Changing models means re-imaging. The
  upper mode is part of that recorded identity (`overlay+per-slot-upper`) for
  exactly the same reason: it decides which directory every write lands in.

## 2. Imager (`imager/`)

A Docker build that emits a netboot **kernel + initramfs**.

- The initramfs is busybox-based with the full kernel module tree (decompressed,
  since busybox can't read `.ko.xz`) for broad NIC/storage coverage.
- `/init` (PID 1) brings up networking via DHCP, picks the target disk, streams
  the image from `imager.url=` over HTTP, decompresses it on the fly, writes it
  with `dd`, then reboots/poweroffs.
- Fully configured by kernel command line — see [DEPLOYMENT.md](DEPLOYMENT.md).

## 3. Provisioning server (`server/`)

A `docker compose` stack on host networking:

- **dnsmasq** — proxyDHCP *or* standalone DHCP (configurable), plus TFTP serving
  iPXE binaries. PXE clients chainload iPXE; iPXE (tagged via DHCP option 175)
  is redirected to `http://SERVER/boot.ipxe`.
- **nginx** — serves `boot.ipxe` (rendered from `.env`), the imager
  kernel/initramfs, and the image file from `./output`.

### End-to-end boot chain

```
PXE NIC ─DHCP→ dnsmasq ─(TFTP)→ iPXE binary ─→ iPXE
iPXE ─DHCP(opt.175)→ dnsmasq ─→ http://SERVER/boot.ipxe
iPXE ─HTTP→ imager vmlinuz + initramfs ─boot→ /init
/init ─HTTP→ image.img.zst ─dd→ /dev/sdX ─→ reboot into Debian A/B
```

## Why this shape

- **Image once, deploy many**: every machine gets a byte-identical, pre-built
  A/B image — far faster and more consistent than running an installer per host.
- **Stateless transport**: PXE/iPXE just delivers a kernel+initramfs; the imaging
  logic lives in the initramfs, so it works the same in a VM or on bare metal.
- **Composable & Dockerized**: build and serve are separate; you can build images
  on one host and serve them from another.
