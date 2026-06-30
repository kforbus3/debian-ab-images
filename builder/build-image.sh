#!/bin/bash
#
# Build a bootable Debian A/B disk image.
#
# Layout (GPT, BIOS/GRUB):
#   p1  bios_grub  (1 MiB, raw)        GRUB core
#   p2  BOOT       (ext4, label BOOT)  shared /boot + kernel + grubenv
#   p3  rootfs-a   (ext4)              root slot A  (Debian)
#   p4  rootfs-b   (ext4)              root slot B  (copy of A)
#   p5  overlay    (ext4)              persistent data, grows on first boot
#
# Designed to run inside the privileged builder container (see Dockerfile).
set -euo pipefail

# --- Defaults (override via flags or environment) ---
SUITE="${SUITE:-trixie}"               # Debian 13
ARCH="${ARCH:-amd64}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
HOSTNAME_="${HOSTNAME_:-debian-ab}"
USERNAME="${USERNAME:-debian}"
PASSWORD="${PASSWORD:-debian}"
ROOT_SIZE="${ROOT_SIZE:-3072}"          # MiB per root slot
BOOT_SIZE="${BOOT_SIZE:-512}"           # MiB shared boot
IMAGE_SIZE="${IMAGE_SIZE:-8}"           # GiB total image
OUTPUT="${OUTPUT:-/output/debian-${SUITE}-ab.img}"
EXTRA_PACKAGES="${EXTRA_PACKAGES:-}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
COMPRESS="${COMPRESS:-zstd}"            # zstd | gzip | none

usage() {
    cat <<EOF
Usage: $0 [options]
  --suite NAME        Debian suite (default: $SUITE)
  --arch ARCH         Architecture (default: $ARCH)
  --hostname NAME     Image hostname (default: $HOSTNAME_)
  --username NAME     Login user to create (default: $USERNAME)
  --password PASS     Password for that user (default: $USERNAME)
  --root-size MiB     Size of each root slot (default: $ROOT_SIZE)
  --image-size GiB    Total image size (default: $IMAGE_SIZE)
  --output PATH       Output image path (default: $OUTPUT)
  --packages "a b c"  Extra packages to install
  --ssh-pubkey FILE   Authorized SSH key to install for the user
  --compress MODE     Compress output: zstd|gzip|none (default: $COMPRESS)
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite) SUITE="$2"; shift 2;;
        --arch) ARCH="$2"; shift 2;;
        --hostname) HOSTNAME_="$2"; shift 2;;
        --username) USERNAME="$2"; shift 2;;
        --password) PASSWORD="$2"; shift 2;;
        --root-size) ROOT_SIZE="$2"; shift 2;;
        --image-size) IMAGE_SIZE="$2"; shift 2;;
        --output) OUTPUT="$2"; shift 2;;
        --packages) EXTRA_PACKAGES="$2"; shift 2;;
        --ssh-pubkey) SSH_PUBKEY="$(cat "$2")"; shift 2;;
        --compress) COMPRESS="$2"; shift 2;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 1;;
    esac
done

OVERLAY_DIR="$(cd "$(dirname "$0")/overlay" && pwd)"
RAW="${OUTPUT%.img}.img"
WORK="$(mktemp -d)"
MNT="$WORK/mnt"
BOOTMNT="$WORK/mnt/boot"
LOOP=""

log()  { echo -e "\033[0;32m[build]\033[0m $*"; }
warn() { echo -e "\033[1;33m[build]\033[0m $*"; }

cleanup() {
    set +e
    mountpoint -q "$MNT/dev/pts" && umount "$MNT/dev/pts"
    for m in dev proc sys boot var/lib/overlay; do
        mountpoint -q "$MNT/$m" && umount "$MNT/$m"
    done
    mountpoint -q "$MNT" && umount "$MNT"
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

log "Creating ${IMAGE_SIZE}GiB image at $RAW"
rm -f "$RAW"
truncate -s "${IMAGE_SIZE}G" "$RAW"

log "Partitioning (GPT, BIOS/GRUB, A/B)"
B_START=2
B_END=$((B_START + BOOT_SIZE))
A_END=$((B_END + ROOT_SIZE))
BB_END=$((A_END + ROOT_SIZE))
parted -s "$RAW" mklabel gpt
parted -s "$RAW" mkpart bios     1MiB        ${B_START}MiB
parted -s "$RAW" set 1 bios_grub on
parted -s "$RAW" mkpart BOOT     ext4 ${B_START}MiB ${B_END}MiB
parted -s "$RAW" mkpart rootfs-a ext4 ${B_END}MiB   ${A_END}MiB
parted -s "$RAW" mkpart rootfs-b ext4 ${A_END}MiB   ${BB_END}MiB
parted -s "$RAW" mkpart overlay  ext4 ${BB_END}MiB  100%

LOOP="$(losetup -f --show -P "$RAW")"
log "Loop device: $LOOP"

# In a container there is no udev to create the partition device nodes, so
# create them from sysfs after asking the kernel to re-scan.
partprobe "$LOOP" 2>/dev/null || true
LOOP_BASE="$(basename "$LOOP")"
for n in 1 2 3 4 5; do
    node="${LOOP}p${n}"
    [ -b "$node" ] && continue
    sysdev="/sys/class/block/${LOOP_BASE}p${n}/dev"
    for _ in 1 2 3 4 5; do [ -f "$sysdev" ] && break; sleep 0.3; done
    if [ -f "$sysdev" ]; then
        mm="$(cat "$sysdev")"
        mknod "$node" b "${mm%:*}" "${mm#*:}"
    fi
done
P_BOOT="${LOOP}p2"; P_A="${LOOP}p3"; P_B="${LOOP}p4"; P_OVL="${LOOP}p5"
[ -b "$P_BOOT" ] || { echo "partition nodes missing under $LOOP" >&2; ls -l ${LOOP}* >&2; exit 1; }

log "Formatting filesystems"
mkfs.ext4 -q -L BOOT     "$P_BOOT"
mkfs.ext4 -q -L rootfs-a "$P_A"
mkfs.ext4 -q -L rootfs-b "$P_B"
mkfs.ext4 -q -L overlay  "$P_OVL"

log "Mounting root slot A"
mkdir -p "$MNT"
mount "$P_A" "$MNT"
mkdir -p "$BOOTMNT" "$MNT/var/lib/overlay"
mount "$P_BOOT" "$BOOTMNT"
mount "$P_OVL" "$MNT/var/lib/overlay"

log "Bootstrapping Debian $SUITE ($ARCH)"
debootstrap --arch="$ARCH" --variant=minbase \
    --include=systemd-sysv,ifupdown,netbase \
    "$SUITE" "$MNT" "$MIRROR"

log "Binding pseudo-filesystems for chroot"
mount --bind /dev "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
mount -t proc proc "$MNT/proc"
mount -t sysfs sys "$MNT/sys"

# Overlay structure for persistent data.
mkdir -p "$MNT/var/lib/overlay/upper" "$MNT/var/lib/overlay/work" "$MNT/var/lib/overlay/persistent"

log "Writing base configuration"
echo "$HOSTNAME_" > "$MNT/etc/hostname"
cat > "$MNT/etc/hosts" <<EOF
127.0.0.1   localhost
127.0.1.1   $HOSTNAME_
::1         localhost ip6-localhost ip6-loopback
EOF

cat > "$MNT/etc/fstab" <<EOF
# <file system>            <mount point>      <type> <options>      <dump> <pass>
LABEL=BOOT                 /boot              ext4   defaults       0      2
LABEL=overlay              /var/lib/overlay   ext4   defaults       0      2
tmpfs                      /tmp               tmpfs  defaults       0      0
EOF

cat > "$MNT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main contrib non-free-firmware
deb $MIRROR ${SUITE}-updates main contrib non-free-firmware
deb http://security.debian.org/debian-security ${SUITE}-security main contrib non-free-firmware
EOF

# DHCP on all ethernet interfaces (predictable + eth*).
cat > "$MNT/etc/systemd/network/10-dhcp.network" <<EOF
[Match]
Name=en* eth*

[Network]
DHCP=yes
EOF

log "Installing kernel, bootloader, and tooling in chroot"
cat > "$MNT/tmp/setup.sh" <<CHROOT
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    linux-image-${ARCH} grub-pc grub-pc-bin \
    openssh-server sudo ca-certificates \
    systemd-resolved cloud-guest-utils gdisk parted e2fsprogs \
    rauc ${EXTRA_PACKAGES}
systemctl enable ssh systemd-networkd systemd-resolved

# Create the login user with sudo.
useradd -m -s /bin/bash -G sudo "${USERNAME}"
echo "${USERNAME}:${PASSWORD}" | chpasswd
# Lock the root account (login via the sudo user).
passwd -l root
CHROOT
chroot "$MNT" bash /tmp/setup.sh
rm -f "$MNT/tmp/setup.sh"

if [ -n "$SSH_PUBKEY" ]; then
    log "Installing SSH authorized key for $USERNAME"
    install -d -m700 "$MNT/home/$USERNAME/.ssh"
    echo "$SSH_PUBKEY" > "$MNT/home/$USERNAME/.ssh/authorized_keys"
    chmod 600 "$MNT/home/$USERNAME/.ssh/authorized_keys"
    chroot "$MNT" chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/.ssh"
fi

log "Applying overlay files (RAUC, GRUB, first-boot expand)"
cp -a "$OVERLAY_DIR"/etc/. "$MNT/etc/"
cp -a "$OVERLAY_DIR"/usr/. "$MNT/usr/"
chmod +x "$MNT/usr/local/sbin/first-boot-expand.sh"
chroot "$MNT" systemctl enable first-boot-expand.service

# A placeholder keyring so RAUC is valid out of the box; replace for real updates.
[ -f "$MNT/etc/rauc/keyring.pem" ] || cp "$MNT/etc/ssl/certs/ca-certificates.crt" "$MNT/etc/rauc/keyring.pem" 2>/dev/null || touch "$MNT/etc/rauc/keyring.pem"

log "Installing GRUB to $LOOP and writing A/B config"
chroot "$MNT" grub-install --target=i386-pc --boot-directory=/boot --recheck "$LOOP"
# Our A/B grub.cfg + a fresh grubenv (kernels are shared on BOOT). Substitute the
# real kernel version so GRUB references the exact /boot/vmlinuz-<ver> files.
KVER="$(ls "$BOOTMNT" | sed -n 's/^vmlinuz-//p' | head -n1)"
[ -n "$KVER" ] || { echo "no kernel found on BOOT partition" >&2; exit 1; }
log "Kernel version: $KVER"
sed "s/__KVER__/$KVER/g" "$OVERLAY_DIR/boot/grub/grub.cfg" > "$BOOTMNT/grub/grub.cfg"
chroot "$MNT" grub-editenv /boot/grub/grubenv create
chroot "$MNT" grub-editenv /boot/grub/grubenv set ORDER="A B" A_TRY=0 B_TRY=0

log "Syncing root slot A -> slot B"
umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys"
umount "$MNT/var/lib/overlay"
umount "$BOOTMNT"
mkdir -p "$WORK/b"
mount "$P_B" "$WORK/b"
# Copy the OS (slot A) into slot B; /boot and /var/lib/overlay are empty mountpoints here.
rsync -aHAX --numeric-ids "$MNT"/ "$WORK/b"/
umount "$WORK/b"
umount "$MNT"
losetup -d "$LOOP"; LOOP=""

log "Image built: $RAW"
case "$COMPRESS" in
    zstd) log "Compressing with zstd"; zstd -f -19 -T0 --rm "$RAW" -o "${RAW}.zst"; OUT="${RAW}.zst";;
    gzip) log "Compressing with gzip"; gzip -f "$RAW"; OUT="${RAW}.gz";;
    none) OUT="$RAW";;
    *) warn "Unknown compression '$COMPRESS', leaving raw"; OUT="$RAW";;
esac

log "Done: $OUT"
ls -lh "$OUT"
