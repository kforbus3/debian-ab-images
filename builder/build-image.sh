#!/bin/bash
#
# Build a bootable Debian or Ubuntu A/B disk image (optionally LUKS-encrypted).
#
# Layout (GPT, hybrid BIOS + UEFI boot):
#   p1  bios_grub  (1 MiB, raw)        GRUB BIOS core
#   p2  ESP        (vfat, label EFI)   EFI system partition (GRUB at removable path)
#   p3  BOOT       (ext4, label BOOT)  shared /boot + kernel + grubenv (always plaintext)
#   p4  rootfs-a   root slot A         (ext4, or LUKS2 + ext4 when --encrypt)
#   p5  rootfs-b   root slot B         (copy of A)
#   p6  overlay    persistent data     (grows to fill the disk on first boot)
#
# Runs inside the privileged builder container (see Dockerfile).
set -euo pipefail

# --- Defaults (override via flags or environment) ---
DISTRO="${DISTRO:-}"                    # debian | ubuntu (empty = auto-detect from suite)
SUITE="${SUITE:-trixie}"
ARCH="${ARCH:-amd64}"
MIRROR="${MIRROR:-}"                    # empty = distro default
HOSTNAME_="${HOSTNAME_:-}"              # empty = <distro>-ab
USERNAME="${USERNAME:-debian}"
PASSWORD="${PASSWORD:-debian}"
ROOT_SIZE="${ROOT_SIZE:-3072}"
BOOT_SIZE="${BOOT_SIZE:-512}"
ESP_SIZE="${ESP_SIZE:-128}"
OVERLAY_MIN="${OVERLAY_MIN:-256}"       # overlay grows to fill the disk on first boot
IMAGE_SIZE="${IMAGE_SIZE:-auto}"        # GiB, or "auto" = smallest possible
OUTPUT="${OUTPUT:-}"                    # empty = /output/<distro>-<suite>-ab.img
EXTRA_PACKAGES="${EXTRA_PACKAGES:-}"
OVERLAY_D="${OVERLAY_D:-/overlay.d}"     # your files, copied over the whole root
RUN_SCRIPT="${RUN_SCRIPT:-}"             # script run inside the chroot at the end
OWN_PATHS="${OWN_PATHS:-}"               # paths the image owns; see image-owned.list
SSH_PUBKEY="${SSH_PUBKEY:-}"
# --- writable-state layout (see the state manifest, further down) ------------
# The default model is the one this project started with: the whole root is a
# single overlay over the A/B slot, shared by both slots, and the paths the
# distribution owns are cleared from it whenever the slot changes.
STATE_MODEL="${STATE_MODEL:-overlay}"
MOUNT_DIRECTIVES="overlay /"
# Cleared from the writable state on a slot change. Everything the distribution
# owns: a copy of these from the previous release would shadow the one the
# update just installed, and nothing in the running system would say so.
RESET_PATHS="/usr /bin /sbin /lib /lib32 /lib64 /libx32 /boot
             /var/lib/dpkg /var/lib/apt /var/cache/apt"
# Held back from that clearing. /usr/local sits inside /usr but is reserved by
# the FHS for locally installed software, so it is the machine's, not the
# image's -- clearing /usr wholesale used to take it, and a script left in
# /usr/local/bin vanished on the first update with nothing said.
KEEP_PATHS="/usr/local"
SSH_KEY_ONLY="${SSH_KEY_ONLY:-false}"
COMPRESS="${COMPRESS:-zstd}"
# Encryption
ENCRYPT="${ENCRYPT:-false}"
UNLOCK="${UNLOCK:-keyfile}"             # passphrase | keyfile | tpm2 | tang
LUKS_PASS="${LUKS_PASS:-}"
TANG_URL="${TANG_URL:-}"

usage() {
    cat <<EOF
Usage: $0 [options]
  --distro NAME           debian|ubuntu (default: auto-detect from --suite)
  --suite NAME            Debian/Ubuntu suite (default: $SUITE; e.g. trixie, bookworm, noble, jammy)
  --arch ARCH             Architecture (default: $ARCH)
  --mirror URL            APT mirror (default: distro's primary mirror)
  --hostname NAME         Image hostname (default: $HOSTNAME_)
  --username NAME         Login user to create (default: $USERNAME)
  --password PASS         Password for that user (default: $USERNAME)
  --root-size MiB         Size of each root slot (default: $ROOT_SIZE)
  --image-size GiB|auto   Total image size (default: auto = smallest possible;
                          the overlay partition expands to fill the target disk
                          on first boot either way)
  --output PATH           Output image path
  --packages "a b c"      Extra packages to install
  --overlay-dir DIR       Directory copied over the image root (default: $OVERLAY_D)
  --run-script FILE       Shell script run inside the chroot after packages
  --own-path PATH         Path the image owns: cleared from the persistent
                          overlay on update so the image version wins.
                          Repeatable. Everything in --overlay-dir is implied.
  --ssh-pubkey FILE       Authorized SSH key file for the user
  --ssh-authorized-key K  Authorized SSH key passed inline
  --ssh-key-only          Disable SSH password auth (requires an SSH key)
  --compress MODE         zstd|gzip|none (default: $COMPRESS)
  --encrypt               LUKS2-encrypt the root slots and overlay
  --unlock METHOD         passphrase|keyfile|tpm2|tang (default: $UNLOCK)
  --luks-passphrase PASS  LUKS passphrase (recovery + setup); required with --encrypt
  --luks-passphrase-file F  Read the passphrase from a file (or - for stdin) instead.
                          Prefer this over --luks-passphrase: an argument is visible
                          in \`ps\` to every user on the build host.
  --tang-url URL          Tang server URL (required for --unlock tang)
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --distro) DISTRO="$2"; shift 2;;
        --suite) SUITE="$2"; shift 2;;
        --arch) ARCH="$2"; shift 2;;
        --mirror) MIRROR="$2"; shift 2;;
        --hostname) HOSTNAME_="$2"; shift 2;;
        --username) USERNAME="$2"; shift 2;;
        --password) PASSWORD="$2"; shift 2;;
        --root-size) ROOT_SIZE="$2"; shift 2;;
        --image-size) IMAGE_SIZE="$2"; shift 2;;
        --output) OUTPUT="$2"; shift 2;;
        --packages) EXTRA_PACKAGES="$2"; shift 2;;
        --overlay-dir) OVERLAY_D="$2"; shift 2;;
        --run-script) RUN_SCRIPT="$2"; shift 2;;
        --own-path) OWN_PATHS="$OWN_PATHS $2"; shift 2;;
        --ssh-pubkey) SSH_PUBKEY="$(cat "$2")"; shift 2;;
        --ssh-authorized-key) SSH_PUBKEY="$2"; shift 2;;
        --ssh-key-only) SSH_KEY_ONLY=true; shift;;
        --compress) COMPRESS="$2"; shift 2;;
        --encrypt) ENCRYPT=true; shift;;
        --unlock) UNLOCK="$2"; shift 2;;
        --luks-passphrase) LUKS_PASS="$2"; shift 2;;
        --luks-passphrase-file)
            # Only the first line, and without its newline: a passphrase pasted
            # into a file almost always ends with one, and including it produces
            # a container that rejects the passphrase the operator believes they
            # set -- discovered at an initramfs prompt, not here.
            if [ "$2" = "-" ]; then IFS= read -r LUKS_PASS || true
            else
                [ -r "$2" ] || die "--luks-passphrase-file: cannot read '$2'"
                IFS= read -r LUKS_PASS < "$2" || true
            fi
            [ -n "$LUKS_PASS" ] || die "--luks-passphrase-file: '$2' is empty"
            shift 2;;
        --tang-url) TANG_URL="$2"; shift 2;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 1;;
    esac
done

log()  { echo -e "\033[0;32m[build]\033[0m $*"; }
warn() { echo -e "\033[1;33m[build]\033[0m $*"; }
die()  { echo -e "\033[0;31m[build] ERROR:\033[0m $*" >&2; exit 1; }

# Machine-readable progress. The web UI parses these lines into a progress bar;
# on a terminal they read as ordinary step markers. Emitted in addition to the
# human log so neither consumer depends on parsing prose.
BUILD_STEP=0
BUILD_STEPS=14
step() {
    BUILD_STEP=$((BUILD_STEP + 1))
    printf '[progress] %d/%d %s\n' "$BUILD_STEP" "$BUILD_STEPS" "$1"
    log "$1"
}

# --- Resolve distro (auto-detect from suite when not given) ---
if [ -z "$DISTRO" ]; then
    case "$SUITE" in
        bionic|focal|jammy|noble|oracular|plucky|questing|resolute) DISTRO=ubuntu;;
        *) DISTRO=debian;;
    esac
fi
# Everything that differs between architectures is decided here rather than
# scattered through the build. amd64 keeps the hybrid BIOS+UEFI boot the fleet
# relies on; arm64 has no BIOS to fall back to and is UEFI-only, with its own
# GRUB target and fallback binary name.
case "$ARCH" in
    amd64)
        GRUB_PKGS="grub-pc grub-pc-bin grub-efi-amd64-bin"
        GRUB_EFI_TARGET="x86_64-efi"
        GRUB_BIOS=1
        QEMU_ARCH="x86_64"
        ;;
    arm64)
        GRUB_PKGS="grub-efi-arm64 grub-efi-arm64-bin"
        GRUB_EFI_TARGET="arm64-efi"
        GRUB_BIOS=0
        QEMU_ARCH="aarch64"
        ;;
    *) die "--arch must be amd64 or arm64 (got '$ARCH')";;
esac

# Cross-building needs the target architecture's interpreter registered with
# binfmt_misc on the host; the builder image ships the static qemu binaries but
# cannot register them itself. Checked here so the failure is one clear line
# rather than "Exec format error" a thousand lines into debootstrap.
if [ "$ARCH" != "$(dpkg --print-architecture)" ]; then
    if [ ! -e "/proc/sys/fs/binfmt_misc/qemu-${QEMU_ARCH}" ]; then
        die "building $ARCH on $(dpkg --print-architecture) needs binfmt support.
    Run once on the host:  docker run --privileged --rm tonistiigi/binfmt --install all"
    fi
    log "Cross-building $ARCH via qemu-${QEMU_ARCH} (binfmt registered)"
fi

case "$DISTRO" in
    debian)
        MIRROR="${MIRROR:-http://deb.debian.org/debian}"
        KERNEL_PKG="linux-image-${ARCH}"
        DEBOOTSTRAP_OPTS=""
        ;;
    ubuntu)
        MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}"
        # Ubuntu's generic kernel (linux-image-<arch> is Debian-only); rauc lives
        # in universe, so debootstrap and APT must enable it.
        KERNEL_PKG="linux-image-generic"
        DEBOOTSTRAP_OPTS="--components=main,universe"
        [ -f /usr/share/keyrings/ubuntu-archive-keyring.gpg ] && \
            DEBOOTSTRAP_OPTS="$DEBOOTSTRAP_OPTS --keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg"
        # Newer Ubuntu suites may postdate the builder's debootstrap; every Ubuntu
        # suite script is a symlink to the generic 'gutsy' script anyway.
        if [ ! -e "/usr/share/debootstrap/scripts/$SUITE" ] && [ -e /usr/share/debootstrap/scripts/gutsy ]; then
            ln -s gutsy "/usr/share/debootstrap/scripts/$SUITE"
        fi
        ;;
    *) die "--distro must be debian or ubuntu";;
esac
# Minimum workable root slot, measured per distro. Ubuntu's linux-image-generic
# hard-depends on linux-firmware and linux-modules-extra (~1.7 GiB installed),
# which Debian's linux-image-amd64 does not — so the same 3 GiB slot that is
# comfortable on Debian overflows on Ubuntu partway through initramfs
# generation. Enforced below rather than left to fail deep in a dpkg run.
case "$DISTRO" in
    ubuntu) MIN_ROOT=5120;;
    *)      MIN_ROOT=2560;;
esac

OS_PRETTY="$(tr '[:lower:]' '[:upper:]' <<< "${DISTRO:0:1}")${DISTRO:1}"
HOSTNAME_="${HOSTNAME_:-${DISTRO}-ab}"
OUTPUT="${OUTPUT:-/output/${DISTRO}-${SUITE}-ab.img}"
# A bare filename means "in the output directory". Without this it lands in the
# builder's working directory instead, which is inside the container: the build
# reports success, and the image is thrown away with the container.
case "$OUTPUT" in /*) ;; *) OUTPUT="/output/${OUTPUT}";; esac

# systemd-resolved became a separate package in Debian 12 / Ubuntu 23.10; on
# older suites it ships inside systemd itself.
RESOLVED_PKG="systemd-resolved"
case "$SUITE" in bionic|focal|jammy) RESOLVED_PKG="";; esac

# --- Validate options ---
if [ "$SSH_KEY_ONLY" = true ] && [ -z "$SSH_PUBKEY" ]; then
    die "--ssh-key-only requires an SSH key (--ssh-pubkey or --ssh-authorized-key)"
fi
USE_KEYFILE=false
if [ "$ENCRYPT" = true ]; then
    [ -n "$LUKS_PASS" ] || die "--encrypt requires --luks-passphrase"
    case "$UNLOCK" in
        passphrase) ;;
        keyfile|tpm2|tang) USE_KEYFILE=true;;
        *) die "--unlock must be passphrase|keyfile|tpm2|tang";;
    esac
    [ "$UNLOCK" = tang ] && [ -z "$TANG_URL" ] && die "--unlock tang requires --tang-url"
fi

# Raise rather than refuse: the caller asked for an image, and a slot too small
# to hold the OS is never what they wanted. The image still auto-sizes and the
# overlay still expands on first boot, so the only visible effect is a larger
# file — much better than failing 15 minutes in.
if [ "$ROOT_SIZE" -lt "$MIN_ROOT" ]; then
    warn "root slot ${ROOT_SIZE} MiB is below the ${MIN_ROOT} MiB minimum for $OS_PRETTY; using ${MIN_ROOT} MiB"
    ROOT_SIZE="$MIN_ROOT"
fi

OVERLAY_DIR="$(cd "$(dirname "$0")/overlay" && pwd)"
RAW="${OUTPUT%.img}.img"
WORK="$(mktemp -d)"
MNT="$WORK/mnt"
BOOTMNT="$WORK/mnt/boot"
KEYDIR="$WORK/keys"
LOOP=""
MAPPERS=()

cleanup() {
    set +e
    mountpoint -q "$MNT/dev/pts" && umount "$MNT/dev/pts"
    # var/cache/apt/archives is the APT cache bind mount; it is nested under
    # $MNT and must come off before $MNT itself, or the final umount fails and
    # the loop device stays attached.
    for m in var/cache/apt/archives dev proc sys boot/efi boot var/lib/overlay; do
        mountpoint -q "$MNT/$m" && umount "$MNT/$m"
    done
    mountpoint -q "$WORK/b" && umount "$WORK/b"
    mountpoint -q "$MNT" && umount "$MNT"
    for m in "${MAPPERS[@]}"; do
        [ -e "/dev/mapper/$m" ] && cryptsetup close "$m" 2>/dev/null
    done
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

log "Building image  distro=$DISTRO suite=$SUITE  encrypt=$ENCRYPT  unlock=$([ "$ENCRYPT" = true ] && echo "$UNLOCK" || echo n/a)  ssh-key-only=$SSH_KEY_ONLY"

E_START=2
E_END=$((E_START + ESP_SIZE))
B_END=$((E_END + BOOT_SIZE))
A_END=$((B_END + ROOT_SIZE))
BB_END=$((A_END + ROOT_SIZE))
MIN_MIB=$((BB_END + OVERLAY_MIN + 1))   # +1 MiB tail for the backup GPT
if [ "$IMAGE_SIZE" = auto ]; then
    TOTAL_MIB=$MIN_MIB
    log "Auto image size: ${TOTAL_MIB} MiB (overlay expands to fill the target disk on first boot)"
else
    TOTAL_MIB=$((IMAGE_SIZE * 1024))
    [ "$TOTAL_MIB" -ge "$MIN_MIB" ] || \
        die "--image-size ${IMAGE_SIZE}G too small: layout needs ${MIN_MIB} MiB (reduce --root-size, or use --image-size auto)"
fi
rm -f "$RAW"
truncate -s "${TOTAL_MIB}M" "$RAW"

step "Partitioning (GPT, hybrid BIOS+UEFI, A/B)"
parted -s "$RAW" mklabel gpt
parted -s "$RAW" mkpart bios     1MiB ${E_START}MiB
parted -s "$RAW" set 1 bios_grub on
parted -s "$RAW" mkpart ESP      fat32 ${E_START}MiB ${E_END}MiB
parted -s "$RAW" set 2 esp on
parted -s "$RAW" mkpart BOOT     ext4 ${E_END}MiB    ${B_END}MiB
parted -s "$RAW" mkpart rootfs-a ext4 ${B_END}MiB    ${A_END}MiB
parted -s "$RAW" mkpart rootfs-b ext4 ${A_END}MiB    ${BB_END}MiB
parted -s "$RAW" mkpart overlay  ext4 ${BB_END}MiB   100%

# Docker gives this container a private /dev that no udev populates, so the loop
# device the kernel hands out often has no node here and losetup fails with
# "device node /dev/loopN (7:N) is lost. You may use mknod(1) to recover it."
# Create the nodes ourselves. (Docker Desktop pre-creates loop0-3 in its VM,
# which is why this can appear to work on a Mac and fail on a Linux host — and
# why it would fail anywhere once those four are busy.)
modprobe loop 2>/dev/null || true
[ -e /dev/loop-control ] || mknod /dev/loop-control c 10 237 2>/dev/null || true
for i in $(seq 0 15); do
    [ -e "/dev/loop$i" ] || mknod "/dev/loop$i" b 7 "$i" 2>/dev/null || true
done

LOOP="$(losetup -f --show -P "$RAW")" || die \
    "could not attach a loop device. The builder needs --privileged and a host
kernel with the loop module available (modprobe loop)."
log "Loop device: $LOOP"
partprobe "$LOOP" 2>/dev/null || true
LOOP_BASE="$(basename "$LOOP")"
for n in 1 2 3 4 5 6; do
    node="${LOOP}p${n}"
    [ -b "$node" ] && continue
    sysdev="/sys/class/block/${LOOP_BASE}p${n}/dev"
    for _ in 1 2 3 4 5; do [ -f "$sysdev" ] && break; sleep 0.3; done
    [ -f "$sysdev" ] && { mm="$(cat "$sysdev")"; mknod "$node" b "${mm%:*}" "${mm#*:}"; }
done
P_ESP="${LOOP}p2"; P_BOOT="${LOOP}p3"; P_A="${LOOP}p4"; P_B="${LOOP}p5"; P_OVL="${LOOP}p6"
[ -b "$P_BOOT" ] || { echo "partition nodes missing under $LOOP" >&2; ls -l ${LOOP}* >&2; exit 1; }

# --- Set up encryption (or plain) backing devices ---
# DEV_* is the device we mkfs/mount (a mapper when encrypted). BOOT is always plain.
DEV_A="$P_A"; DEV_B="$P_B"; DEV_OVL="$P_OVL"
if [ "$ENCRYPT" = true ]; then
    log "Encrypting root slots and overlay (LUKS2)"
    mkdir -p "$KEYDIR"
    [ "$USE_KEYFILE" = true ] && { head -c 4096 /dev/urandom > "$KEYDIR/keyfile"; chmod 400 "$KEYDIR/keyfile"; }
    # Use PBKDF2 (not memory-hard Argon2id) so the root volume can be unlocked in
    # the low-memory early-boot initramfs on any target. The high-entropy keyfile
    # / TPM / Tang key makes KDF hardness irrelevant; the passphrase slot still
    # gets strong iteration counts.
    PBKDF_OPTS="--pbkdf pbkdf2 --pbkdf-force-iterations 200000"
    luks_setup() {  # $1=partition $2=mapper-name
        printf '%s' "$LUKS_PASS" | cryptsetup luksFormat --type luks2 $PBKDF_OPTS --batch-mode "$1" -
        printf '%s' "$LUKS_PASS" | cryptsetup open "$1" "$2" -
        MAPPERS+=("$2")
        if [ "$USE_KEYFILE" = true ]; then
            printf '%s' "$LUKS_PASS" | cryptsetup luksAddKey $PBKDF_OPTS --key-file=- "$1" "$KEYDIR/keyfile"
        fi
    }
    luks_setup "$P_A"   luks-rootfs-a rootfs-a
    luks_setup "$P_B"   luks-rootfs-b rootfs-b
    luks_setup "$P_OVL" luks-overlay  overlay
    DEV_A=/dev/mapper/luks-rootfs-a
    DEV_B=/dev/mapper/luks-rootfs-b
    DEV_OVL=/dev/mapper/luks-overlay
fi

step "Formatting filesystems"
# The builder runs Debian trixie, whose mke2fs (1.47.x) enables orphan_file and
# metadata_csum_seed by default. GRUB 2.06 — what Ubuntu 22.04 ships — cannot
# read either, so grub-install dies with a bare "error: unknown filesystem"; the
# target's own older e2fsprogs would fail to fsck them too. Debian trixie's GRUB
# 2.12 copes, which is exactly why this only ever broke Ubuntu images. Turning
# both off costs nothing measurable and keeps images readable by older tooling.
EXT4_COMPAT="^orphan_file,^metadata_csum_seed"
mkfs.vfat -F32 -n EFI    "$P_ESP" >/dev/null
mkfs.ext4 -q -O "$EXT4_COMPAT" -L BOOT     "$P_BOOT"
mkfs.ext4 -q -O "$EXT4_COMPAT" -L rootfs-a "$DEV_A"
mkfs.ext4 -q -O "$EXT4_COMPAT" -L rootfs-b "$DEV_B"
mkfs.ext4 -q -O "$EXT4_COMPAT" -L overlay  "$DEV_OVL"

step "Mounting root slot A"
mkdir -p "$MNT"
mount "$DEV_A" "$MNT"
mkdir -p "$BOOTMNT" "$MNT/var/lib/overlay"
mount "$P_BOOT" "$BOOTMNT"
mkdir -p "$BOOTMNT/efi"
mount "$P_ESP" "$BOOTMNT/efi"
mount "$DEV_OVL" "$MNT/var/lib/overlay"

step "Bootstrapping $OS_PRETTY $SUITE ($ARCH)"
debootstrap --arch="$ARCH" --variant=minbase $DEBOOTSTRAP_OPTS \
    --include=systemd-sysv,ifupdown,netbase \
    "$SUITE" "$MNT" "$MIRROR"

step "Binding pseudo-filesystems for chroot"
mount --bind /dev "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
mount -t proc proc "$MNT/proc"
mount -t sysfs sys "$MNT/sys"

# upper and work are the overlay root's two layers. There is no third
# directory: a "persistent" one was created here and never used by anything,
# which read as a supported place to put data that nothing would have kept.
mkdir -p "$MNT/var/lib/overlay/upper" "$MNT/var/lib/overlay/work"

step "Writing base configuration"
echo "$HOSTNAME_" > "$MNT/etc/hostname"
cat > "$MNT/etc/hosts" <<EOF
127.0.0.1   localhost
127.0.1.1   $HOSTNAME_
::1         localhost ip6-localhost ip6-loopback
EOF

cat > "$MNT/etc/fstab" <<EOF
# <file system>            <mount point>      <type> <options>      <dump> <pass>
LABEL=BOOT                 /boot              ext4   defaults       0      2
LABEL=EFI                  /boot/efi          vfat   umask=0077     0      1
# The initramfs already mounts this and binds it here before switching root,
# so the entry is x-systemd.automount-free and marked nofail: it is a no-op
# on an overlay-root boot, and the real mount when booted with ab.overlay=off.
LABEL=overlay              /var/lib/overlay   ext4   defaults,nofail 0     2
tmpfs                      /tmp               tmpfs  defaults       0      0
EOF

if [ "$DISTRO" = ubuntu ]; then
    cat > "$MNT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main universe
deb $MIRROR ${SUITE}-updates main universe
deb http://security.ubuntu.com/ubuntu ${SUITE}-security main universe
EOF
else
    cat > "$MNT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main contrib non-free-firmware
deb $MIRROR ${SUITE}-updates main contrib non-free-firmware
deb http://security.debian.org/debian-security ${SUITE}-security main contrib non-free-firmware
EOF
fi

cat > "$MNT/etc/systemd/network/10-dhcp.network" <<EOF
[Match]
Name=en* eth*

[Network]
DHCP=yes
EOF

# --- crypttab + key material (before installing the initramfs) ---
CRYPT_PACKAGES=""
if [ "$ENCRYPT" = true ]; then
    CRYPT_PACKAGES="cryptsetup cryptsetup-initramfs"
    [ "$UNLOCK" = tpm2 ] && CRYPT_PACKAGES="$CRYPT_PACKAGES tpm2-tools libtss2-tcti-device0 systemd-cryptsetup"
    [ "$UNLOCK" = tang ] && CRYPT_PACKAGES="$CRYPT_PACKAGES clevis clevis-luks clevis-initramfs curl"

    UUID_A="$(cryptsetup luksUUID "$P_A")"
    UUID_B="$(cryptsetup luksUUID "$P_B")"
    UUID_OVL="$(cryptsetup luksUUID "$P_OVL")"

    if [ "$USE_KEYFILE" = true ]; then
        # Bootstrap unlock via a keyfile embedded in the initramfs. For tpm2/tang
        # this only bootstraps the first boot; the enrollment service then binds
        # the TPM/Tang and removes the keyfile.
        install -d -m700 "$MNT/etc/cryptsetup-keys.d"
        for n in rootfs-a rootfs-b overlay; do
            install -m400 "$KEYDIR/keyfile" "$MNT/etc/cryptsetup-keys.d/luks-$n.key"
        done
        KEYREF_A="/etc/cryptsetup-keys.d/luks-rootfs-a.key"
        KEYREF_B="/etc/cryptsetup-keys.d/luks-rootfs-b.key"
        KEYREF_OVL="/etc/cryptsetup-keys.d/luks-overlay.key"
    else
        KEYREF_A=none; KEYREF_B=none; KEYREF_OVL=none
    fi

    NETOPT=""
    [ "$UNLOCK" = tang ] && NETOPT=",_netdev"
    # `initramfs` on every entry is what actually gets these devices unlocked
    # early. cryptsetup-initramfs otherwise includes only the device it resolves
    # as root at build time -- which is slot A, because that is what the builder
    # is standing in. Without the option, booting slot B cannot unlock its own
    # root, and the overlay is not opened until well after the switch to root,
    # far too late to serve as root's upper layer.
    cat > "$MNT/etc/crypttab" <<EOF
# <name>          <device>                 <keyfile>     <options>
luks-rootfs-a     UUID=$UUID_A             $KEYREF_A     luks,discard,initramfs$NETOPT
luks-rootfs-b     UUID=$UUID_B             $KEYREF_B     luks,discard,initramfs$NETOPT
luks-overlay      UUID=$UUID_OVL           $KEYREF_OVL   luks,discard,initramfs$NETOPT
EOF
fi

step "Installing kernel, bootloader, and tooling in chroot"
# Keep APT's downloaded .debs OUT of the root slot. Ubuntu pulls ~460 MB of
# archives (linux-firmware and linux-modules-extra dominate), and holding those
# alongside the unpacked files is enough on its own to exhaust a 3 GiB slot —
# initramfs generation then dies with a bare "No space left on device". The
# cache lives on the builder's own filesystem instead and is discarded after.
APTCACHE="$WORK/aptcache"
mkdir -p "$APTCACHE" "$MNT/var/cache/apt/archives"
mount --bind "$APTCACHE" "$MNT/var/cache/apt/archives"

cat > "$MNT/tmp/setup.sh" <<CHROOT
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
# initramfs-tools is explicit: Debian kernels depend on it, Ubuntu kernels only
# recommend it, and without it no initrd.img is generated for GRUB to load.
apt-get install -y --no-install-recommends \
    ${KERNEL_PKG} initramfs-tools ${GRUB_PKGS} \
    openssh-server sudo ca-certificates curl \
    ${RESOLVED_PKG} cloud-guest-utils gdisk parted e2fsprogs \
    rauc ${CRYPT_PACKAGES} ${EXTRA_PACKAGES}

# Debian splits RAUC in two: the rauc package is the command, rauc-service is
# the D-Bus service it talks to. With only the former, "rauc install" and
# "rauc status" both fail with "de.pengutronix.rauc was not provided by any
# .service files" -- so the machine can never be updated, and nothing says why
# until someone tries. Best-effort because not every suite packages it
# separately; where it does not, the service is part of the rauc package.
#
# No backticks anywhere in this block: the heredoc below is unquoted, so they
# would be command substitution executed by the builder, not text.
if apt-cache show rauc-service >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends rauc-service
fi
if [ ! -e /usr/share/dbus-1/system-services/de.pengutronix.rauc.service ]; then
    echo "WARNING: RAUC's D-Bus service is missing; 'rauc install' will not work" >&2
fi

systemctl enable ssh systemd-networkd systemd-resolved

useradd -m -s /bin/bash -G sudo "${USERNAME}"
echo "${USERNAME}:${PASSWORD}" | chpasswd
passwd -l root
CHROOT
if ! chroot "$MNT" bash /tmp/setup.sh; then
    used="$(df -Pm "$MNT" | awk 'NR==2 {print $3}')"
    avail="$(df -Pm "$MNT" | awk 'NR==2 {print $4}')"
    if [ "${avail:-1}" -lt 64 ]; then
        die "the root slot filled up while installing packages (${used} MiB used, \
${avail} MiB free in a ${ROOT_SIZE} MiB slot).
Rebuild with a larger --root-size — $OS_PRETTY $SUITE needs about ${MIN_ROOT} MiB \
for the base system, kernel and initramfs before any extra packages."
    fi
    die "package installation failed in the chroot (see the apt/dpkg output above)"
fi
rm -f "$MNT/tmp/setup.sh"
umount "$MNT/var/cache/apt/archives"
rm -rf "$APTCACHE"

# Every machine imaged from this build must get its own identity. Blank the
# machine-id and drop the build-time SSH host keys; machine-identity.service
# regenerates them on first boot and persists them in the overlay so they
# survive A/B slot switches and updates.
step "Resetting machine identity (machine-id, SSH host keys)"
truncate -s0 "$MNT/etc/machine-id"
install -d "$MNT/var/lib/dbus"
ln -sf /etc/machine-id "$MNT/var/lib/dbus/machine-id"
rm -f "$MNT"/etc/ssh/ssh_host_*

# --- SSH key + key-only hardening ---
if [ -n "$SSH_PUBKEY" ]; then
    log "Installing SSH authorized key for $USERNAME"
    install -d -m700 "$MNT/home/$USERNAME/.ssh"
    echo "$SSH_PUBKEY" > "$MNT/home/$USERNAME/.ssh/authorized_keys"
    chmod 600 "$MNT/home/$USERNAME/.ssh/authorized_keys"
    chroot "$MNT" chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/.ssh"
fi
if [ "$SSH_KEY_ONLY" = true ]; then
    log "Disabling SSH password authentication (key-only)"
    install -d -m755 "$MNT/etc/ssh/sshd_config.d"
    cat > "$MNT/etc/ssh/sshd_config.d/50-key-only.conf" <<EOF
# Key-only SSH (set at build time by --ssh-key-only)
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
EOF
fi

step "Applying overlay files (RAUC, GRUB, first-boot expand, LUKS enroll)"
cp -a "$OVERLAY_DIR"/etc/. "$MNT/etc/"
# initramfs-tools silently ignores a script that is not executable, which would
# leave the root overlay off with nothing in the log to say why.
chmod 0755 "$MNT/etc/initramfs-tools/scripts/local-bottom/ab-overlay" \
           "$MNT/etc/initramfs-tools/hooks/ab-overlay" 2>/dev/null || true
cp -a "$OVERLAY_DIR"/usr/. "$MNT/usr/"
# RAUC bundles are only accepted by systems with a matching compatible string.
sed -i "s/^compatible=.*/compatible=${DISTRO}-ab/" "$MNT/etc/rauc/system.conf"
# On an encrypted image the partition IS the LUKS container, so leaving RAUC
# pointed at /dev/disk/by-partlabel/rootfs-* would have it make a filesystem
# straight over the LUKS header -- destroying the slot rather than updating
# it. Point it at the unlocked mappings instead; the initramfs opens all of
# them (every crypttab entry carries the `initramfs` option), so both slots
# are present at runtime, not just the booted one.

# --- your files, and the paths the image owns -------------------------------
#
# Copied over the WHOLE root, not just etc/ and usr/ like the project's own
# overlay above, and applied after it so your version of a file wins.
#
# Everything shipped here is also recorded as image-owned. That is the half that
# makes "override whatever is on the machine" true rather than merely intended:
# the root filesystem is an overlay, and a file already present in the machine's
# upper layer shadows the image's copy -- so an update would install your
# /etc/hosts and the machine would keep using its own. Recording the path lets
# the initramfs drop that shadowing copy on the next update, at which point the
# image's version is what the machine actually reads.
OWNED_LIST="$MNT/usr/lib/ab/image-owned.list"
mkdir -p "$(dirname "$OWNED_LIST")"
: > "$OWNED_LIST"

if [ -d "$OVERLAY_D" ] && [ -n "$(ls -A "$OVERLAY_D" 2>/dev/null)" ]; then
    step "Applying your overlay from $OVERLAY_D"
    # The directory's own README explains the directory; it is not part of the
    # image, and copying it would put a stray /README.md on every machine.
    ( cd "$OVERLAY_D" && tar -cf - --exclude=./README.md . ) | tar -xf - -C "$MNT"
    # Record every file (not directory): clearing a whole directory from the
    # upper layer would delete machine-local files that live alongside yours --
    # dropping every netplan on the machine when you only shipped one.
    ( cd "$OVERLAY_D" && find . \( -type f -o -type l \) ! -name README.md ) \
        | sed 's|^\.||' | sort >> "$OWNED_LIST"
    log "  $(wc -l < "$OWNED_LIST") file(s) will override the machine's own copies on update"
fi

for p in $OWN_PATHS; do
    case "$p" in
        /*) printf '%s\n' "$p" >> "$OWNED_LIST";;
        *)  die "--own-path must be absolute (got '$p')";;
    esac
done
if [ -s "$OWNED_LIST" ]; then
    sort -u "$OWNED_LIST" -o "$OWNED_LIST"
fi

# --- the state manifest ------------------------------------------------------
#
# Where the machine's writable state lives. The initramfs applies this rather
# than deciding for itself, so the layout is a property of the image and an
# update can change it in the same step that ships the software expecting it.
#
# The default reproduces what the initramfs used to hardcode: the whole root is
# one overlay over the slot, and the paths the distribution owns are cleared
# from it on a slot change so an old release cannot shadow the new one.
#
# Mount directives are emitted parent-first. The initramfs applies them in file
# order and does not sort -- it has busybox and a bad day is a machine that does
# not boot, whereas this has coreutils and can simply get the order right.
STATE_CONF="$MNT/usr/lib/ab/state.conf"
step "Writing the state manifest ($STATE_MODEL)"
{
    echo "# Generated by build-image.sh -- how this image lays out writable state."
    echo "# Applied by /etc/initramfs-tools/scripts/local-bottom/ab-overlay at boot."
    echo "model $STATE_MODEL"
    echo ""
    printf '%s\n' "$MOUNT_DIRECTIVES" | grep -v '^$' | \
        awk '{ n = gsub("/", "/", $2); print n, $0 }' | sort -k1,1n -k3,3 | cut -d' ' -f2-
    echo ""
    for p in $RESET_PATHS;  do echo "reset-on-update $p"; done
    for p in $KEEP_PATHS;   do echo "keep $p"; done
} > "$STATE_CONF"
log "  $(grep -cvE '^\s*(#|$)' "$STATE_CONF") directive(s)"

if [ "$ENCRYPT" = true ]; then
    sed -i "s|^device=/dev/disk/by-partlabel/rootfs-a|device=/dev/mapper/luks-rootfs-a|; \
            s|^device=/dev/disk/by-partlabel/rootfs-b|device=/dev/mapper/luks-rootfs-b|" \
        "$MNT/etc/rauc/system.conf"
fi
chmod +x "$MNT/usr/local/sbin/first-boot-expand.sh" "$MNT/usr/local/sbin/luks-enroll.sh" \
         "$MNT/usr/local/sbin/ab-mark-good.sh" "$MNT/usr/local/sbin/machine-identity.sh" \
         "$MNT/usr/local/sbin/ab-overlay-diff.sh" "$MNT/usr/local/sbin/ab-checkin.sh" \
         "$MNT/usr/local/sbin/ab-update.sh" "$MNT/usr/local/sbin/ab-sync-boot.sh" \
         "$MNT/usr/local/sbin/ab-kernel-hook.sh"
# The others are only ever run by systemd; this one is run by a person, so it
# gets a name without the extension and a place on the default PATH.
ln -sf ab-overlay-diff.sh "$MNT/usr/local/sbin/ab-overlay-diff"
ln -sf ab-update.sh       "$MNT/usr/local/sbin/ab-update"
ln -sf ab-sync-boot.sh    "$MNT/usr/local/sbin/ab-sync-boot"

# Recovery is the thing nobody remembers under pressure, so the machine says it
# on every login rather than leaving it to documentation on another computer.
cat > "$MNT/etc/motd" <<'MOTD'

  A/B image-based system.  Root is an overlay: the image is read-only
  underneath, and everything written since imaging lives on the overlay
  partition, shared by both slots so updates never destroy /home.

    ab-overlay-diff        what this machine changed, and what it hides
    ab-overlay-diff -a     include added and deleted files
    ab-update              install an update into the other slot
    ab-update --status     which slot is running, and what is on the other

  Recovery is in the GRUB menu at boot (hold Shift / press Esc):
    "Recovery: Slot A|B, reset overlay"   start clean, keep a copy in
                                          /var/lib/overlay/upper.prev
    "Recovery: Slot A|B, no overlay"      boot the image exactly as written

MOTD

# --- keep apt from destroying the A/B boot configuration --------------------
#
# grub.cfg here is written by this builder and understood by RAUC: slot order,
# try counters, per-slot kernels, the recovery entries. update-grub regenerates
# it from /etc/grub.d and knows about none of that, and Debian calls update-grub
# from /etc/kernel/postinst.d/zz-update-grub on every kernel install and from
# the grub packages own postinst on upgrade. One "apt upgrade" that pulls a
# kernel would therefore replace the A/B configuration with a generic one --
# no rauc.slot=, no slot selection, no recovery entries -- and the machine would
# come up, if at all, with A/B silently dead.
#
# Diverting the binary covers every caller at once, which grubbing about in
# individual hooks does not: kernel hooks, package postinsts, and anyone typing
# it by hand all get the same answer.
mkdir -p "$MNT/usr/local/sbin"
chroot "$MNT" dpkg-divert --local --rename --add /usr/sbin/update-grub >/dev/null
cat > "$MNT/usr/sbin/update-grub" <<'NOGRUB'
#!/bin/sh
# Deliberately does nothing. This is an A/B image: /boot/grub/grub.cfg is part
# of the image and is replaced by re-imaging, not regenerated on the machine.
# Regenerating it would drop slot selection, the rauc.slot= parameters and the
# recovery entries, leaving a machine that boots -- until you need to roll back.
#
# The real one is still there as /usr/sbin/update-grub.distrib if you genuinely
# need it, but expect to re-image afterwards.
echo "update-grub: skipped; this is an A/B image whose grub.cfg is managed by the image." >&2
exit 0
NOGRUB
chmod 0755 "$MNT/usr/sbin/update-grub"

# A kernel installed by apt is inert here -- GRUB boots the slot's own copy,
# which only a bundle replaces. The hook does not wire the two together on
# purpose: a kernel swapped in underneath a running slot would no longer match
# the root filesystem it was built against. It says so instead, because the
# alternative is a machine that reboots on the old kernel with no explanation.
install -m0755 "$OVERLAY_DIR/usr/local/sbin/ab-kernel-hook.sh" \
    "$MNT/etc/kernel/postinst.d/zz-ab-kernel-notice"

chroot "$MNT" systemctl enable first-boot-expand.service ab-mark-good.service \
                                machine-identity.service ab-checkin.service

# RAUC only installs bundles signed by a certificate in this keyring, and the
# keyring is baked into the image -- so a machine can never be updated by a
# bundle signed after it was built unless that certificate was already inside.
# The signing key is generated once by make-bundle.sh and kept; using it here
# means images and bundles from this repo work together with no extra step.
# Falling back to the CA bundle keeps unsigned-update-free behaviour for images
# built before any key existed, rather than failing the build.
# --- your customization script ----------------------------------------------
#
# Runs inside the chroot, after packages and both overlays, so it can enable a
# unit that was just installed, add a user, or write a file that depends on the
# hostname. Not everything is a file, which is why the overlay alone is not
# enough.
#
# It runs with the image's own filesystem as / but the builder's kernel, so
# anything needing a running system (systemctl start, a daemon) will not work --
# systemctl enable does, because it only writes symlinks.
if [ -n "$RUN_SCRIPT" ]; then
    [ -f "$RUN_SCRIPT" ] || die "--run-script: no such file: $RUN_SCRIPT"
    step "Running your customization script in the chroot"
    install -m0755 "$RUN_SCRIPT" "$MNT/tmp/ab-custom.sh"
    if ! chroot "$MNT" /tmp/ab-custom.sh; then
        rm -f "$MNT/tmp/ab-custom.sh"
        die "your --run-script failed (see its output above); the image was not finished"
    fi
    rm -f "$MNT/tmp/ab-custom.sh"
fi

RAUC_CERT="${RAUC_CERT:-/output/rauc-keys/cert.pem}"
if [ -f "$RAUC_CERT" ]; then
    log "Trusting the update signing certificate ($RAUC_CERT)"
    cp "$RAUC_CERT" "$MNT/etc/rauc/keyring.pem"
elif [ ! -f "$MNT/etc/rauc/keyring.pem" ]; then
    log "WARNING: no signing certificate at $RAUC_CERT — this image will not accept"
    log "         update bundles. Build a bundle once to generate the key, then rebuild."
    cp "$MNT/etc/ssl/certs/ca-certificates.crt" "$MNT/etc/rauc/keyring.pem" 2>/dev/null \
        || touch "$MNT/etc/rauc/keyring.pem"
fi

# Configure first-boot TPM/Tang enrollment.
if [ "$ENCRYPT" = true ] && { [ "$UNLOCK" = tpm2 ] || [ "$UNLOCK" = tang ]; }; then
    log "Enabling first-boot LUKS enrollment ($UNLOCK)"
    cat > "$MNT/etc/luks-enroll.conf" <<EOF
METHOD=$UNLOCK
TANG_URL=$TANG_URL
EOF
    chroot "$MNT" systemctl enable luks-enroll.service
fi

# Rebuild the initramfs so it includes cryptsetup, crypttab, and any keyfiles.
# These config files belong to cryptsetup-initramfs / initramfs-tools, which only
# exist now that the chroot package install has run.
if [ "$ENCRYPT" = true ]; then
    log "Configuring and rebuilding initramfs with cryptsetup support"
    install -d "$MNT/etc/cryptsetup-initramfs"
    # Force ALL crypttab devices into the initramfs so it can unlock whichever
    # A/B slot GRUB selects (not just the slot that was root at build time).
    echo 'CRYPTSETUP=y' >> "$MNT/etc/cryptsetup-initramfs/conf-hook"
    if [ "$USE_KEYFILE" = true ]; then
        echo 'KEYFILE_PATTERN="/etc/cryptsetup-keys.d/*.key"' >> "$MNT/etc/cryptsetup-initramfs/conf-hook"
        echo 'UMASK=0077' >> "$MNT/etc/initramfs-tools/initramfs.conf"
    fi
fi

# The initramfs is generated when the kernel package is installed, which happens
# before the overlay files are copied in -- so it has to be rebuilt here or the
# root-overlay script simply would not be in it. This used to run only for
# encrypted images, which would have left every unencrypted image booting
# without the overlay and no clue as to why.
log "Rebuilding initramfs (root overlay, and cryptsetup where enabled)"
chroot "$MNT" update-initramfs -u

if [ "$GRUB_BIOS" = 1 ]; then
    step "Installing GRUB (BIOS + UEFI) and writing A/B config"
    chroot "$MNT" grub-install --target=i386-pc --boot-directory=/boot --recheck "$LOOP"
else
    step "Installing GRUB (UEFI) and writing A/B config"
fi
# --removable puts GRUB at the firmware's fallback path -- BOOTX64.EFI on amd64,
# BOOTAA64.EFI on arm64 -- so any UEFI firmware boots it without an NVRAM entry.
# Required for mass imaging, where NVRAM cannot be prepared per machine. Secure
# Boot must be disabled.
chroot "$MNT" grub-install --target="$GRUB_EFI_TARGET" --efi-directory=/boot/efi \
    --boot-directory=/boot --removable --no-nvram
KVER="$(ls "$BOOTMNT" | sed -n 's/^vmlinuz-//p' | head -n1)"
[ -n "$KVER" ] || die "no kernel found on BOOT partition"
log "Kernel version: $KVER"

# Each slot gets its own copy of the kernel and initramfs, under a name that
# never changes. /boot is a single shared partition, so without this both slots
# boot the same kernel -- and an update could not deliver a new one without
# replacing the kernel the *running* slot depends on, which would break rollback
# the moment the new slot failed. Per-slot copies mean an update writes only the
# inactive slot's kernel, and falling back to the old slot falls back to its
# kernel too.
#
# The names carry no version, so grub.cfg never has to change: an update
# replaces /A/vmlinuz in place. The versioned originals stay where dpkg put them
# at the top of /boot, because that is where the kernel packages and
# update-initramfs expect to find them.
for sl in A B; do
    mkdir -p "$BOOTMNT/$sl"
    cp -a "$BOOTMNT/vmlinuz-$KVER"    "$BOOTMNT/$sl/vmlinuz"
    cp -a "$BOOTMNT/initrd.img-$KVER" "$BOOTMNT/$sl/initrd.img"
done
log "Per-slot kernels staged: /A and /B"

sed -e "s/__KVER__/$KVER/g" -e "s/__OS__/$OS_PRETTY/g" \
    "$OVERLAY_DIR/boot/grub/grub.cfg" > "$BOOTMNT/grub/grub.cfg"
chroot "$MNT" grub-editenv /boot/grub/grubenv create
# A_OK/B_OK alongside the try counters: RAUC's grub backend reads ORDER,
# <slot>_TRY and <slot>_OK, and without the _OK variables it reports every
# slot as "boot status: bad" and refuses to mark one primary -- so an update
# installs and then cannot be activated. grub.cfg honours them too, so a slot
# explicitly marked bad is skipped rather than booted into a known failure.
chroot "$MNT" grub-editenv /boot/grub/grubenv set ORDER="A B" \
    A_TRY=0 B_TRY=0 A_OK=1 B_OK=1

step "Syncing root slot A -> slot B"
umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys"
umount "$MNT/var/lib/overlay"
umount "$BOOTMNT/efi"
umount "$BOOTMNT"
mkdir -p "$WORK/b"
mount "$DEV_B" "$WORK/b"
rsync -aHAX --numeric-ids "$MNT"/ "$WORK/b"/
umount "$WORK/b"
umount "$MNT"

# Close LUKS mappers before detaching the loop device.
if [ "$ENCRYPT" = true ]; then
    for m in "${MAPPERS[@]}"; do cryptsetup close "$m" 2>/dev/null || true; done
    MAPPERS=()
fi
losetup -d "$LOOP"; LOOP=""

log "Image built: $RAW"
case "$COMPRESS" in
    zstd) step "Compressing with zstd (slowest step on a large image)"; zstd -f -19 -T0 --rm "$RAW" -o "${RAW}.zst"; OUT="${RAW}.zst";;
    gzip) step "Compressing with gzip"; gzip -f "$RAW"; OUT="${RAW}.gz";;
    none) step "Skipping compression"; OUT="$RAW";;
    *) warn "Unknown compression '$COMPRESS', leaving raw"; step "Skipping compression"; OUT="$RAW";;
esac

step "Writing SHA256 checksum and metadata sidecars"
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
cat > "${OUT}.json" <<EOF
{
  "distro": "$DISTRO",
  "suite": "$SUITE",
  "arch": "$ARCH",
  "hostname": "$HOSTNAME_",
  "username": "$USERNAME",
  "image_size_gib": $(awk "BEGIN{printf \"%.2f\", $TOTAL_MIB/1024}"),
  "image_size_mib": $TOTAL_MIB,
  "root_size_mib": $ROOT_SIZE,
  "encrypted": $ENCRYPT,
  "unlock": "$([ "$ENCRYPT" = true ] && echo "$UNLOCK" || echo none)",
  "compress": "$COMPRESS",
  "created": "$(date -u +%FT%TZ)"
}
EOF

step "Done"
[ "$ENCRYPT" = true ] && log "Encryption: LUKS2, unlock=$UNLOCK (passphrase is also enrolled for recovery)"
ls -lh "$OUT" "${OUT}.sha256" "${OUT}.json"
