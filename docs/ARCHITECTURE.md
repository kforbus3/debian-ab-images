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

`/boot` and the kernel are **shared**; A/B applies to the **root filesystem**.
GRUB's prefix is on the `BOOT` partition; `grub.cfg` selects a slot from `ORDER`
and per-slot `TRY` counters in `grubenv`, then boots `root=LABEL=rootfs-a|b`.
This mirrors RAUC's documented GRUB integration, so RAUC can flip slots by editing
`grubenv`.

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
