#!/bin/bash
#
# Build a bootable Debian A/B disk image (optionally LUKS-encrypted).
#
# Layout (GPT, BIOS/GRUB):
#   p1  bios_grub  (1 MiB, raw)        GRUB core
#   p2  BOOT       (ext4, label BOOT)  shared /boot + kernel + grubenv (always plaintext)
#   p3  rootfs-a   root slot A         (ext4, or LUKS2 + ext4 when --encrypt)
#   p4  rootfs-b   root slot B         (copy of A)
#   p5  overlay    persistent data     (grows on first boot)
#
# Runs inside the privileged builder container (see Dockerfile).
set -euo pipefail

# --- Defaults (override via flags or environment) ---
SUITE="${SUITE:-trixie}"
ARCH="${ARCH:-amd64}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
HOSTNAME_="${HOSTNAME_:-debian-ab}"
USERNAME="${USERNAME:-debian}"
PASSWORD="${PASSWORD:-debian}"
ROOT_SIZE="${ROOT_SIZE:-3072}"
BOOT_SIZE="${BOOT_SIZE:-512}"
IMAGE_SIZE="${IMAGE_SIZE:-8}"
OUTPUT="${OUTPUT:-/output/debian-${SUITE}-ab.img}"
EXTRA_PACKAGES="${EXTRA_PACKAGES:-}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
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
  --suite NAME            Debian suite (default: $SUITE)
  --arch ARCH             Architecture (default: $ARCH)
  --hostname NAME         Image hostname (default: $HOSTNAME_)
  --username NAME         Login user to create (default: $USERNAME)
  --password PASS         Password for that user (default: $USERNAME)
  --root-size MiB         Size of each root slot (default: $ROOT_SIZE)
  --image-size GiB        Total image size (default: $IMAGE_SIZE)
  --output PATH           Output image path
  --packages "a b c"      Extra packages to install
  --ssh-pubkey FILE       Authorized SSH key file for the user
  --ssh-authorized-key K  Authorized SSH key passed inline
  --ssh-key-only          Disable SSH password auth (requires an SSH key)
  --compress MODE         zstd|gzip|none (default: $COMPRESS)
  --encrypt               LUKS2-encrypt the root slots and overlay
  --unlock METHOD         passphrase|keyfile|tpm2|tang (default: $UNLOCK)
  --luks-passphrase PASS  LUKS passphrase (recovery + setup); required with --encrypt
  --tang-url URL          Tang server URL (required for --unlock tang)
  -h, --help              Show this help
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
        --ssh-authorized-key) SSH_PUBKEY="$2"; shift 2;;
        --ssh-key-only) SSH_KEY_ONLY=true; shift;;
        --compress) COMPRESS="$2"; shift 2;;
        --encrypt) ENCRYPT=true; shift;;
        --unlock) UNLOCK="$2"; shift 2;;
        --luks-passphrase) LUKS_PASS="$2"; shift 2;;
        --tang-url) TANG_URL="$2"; shift 2;;
        -h|--help) usage; exit 0;;
        *) echo "Unknown option: $1" >&2; usage; exit 1;;
    esac
done

log()  { echo -e "\033[0;32m[build]\033[0m $*"; }
warn() { echo -e "\033[1;33m[build]\033[0m $*"; }
die()  { echo -e "\033[0;31m[build] ERROR:\033[0m $*" >&2; exit 1; }

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
    for m in dev proc sys boot var/lib/overlay; do
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

log "Building image  encrypt=$ENCRYPT  unlock=$([ "$ENCRYPT" = true ] && echo "$UNLOCK" || echo n/a)  ssh-key-only=$SSH_KEY_ONLY"
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
partprobe "$LOOP" 2>/dev/null || true
LOOP_BASE="$(basename "$LOOP")"
for n in 1 2 3 4 5; do
    node="${LOOP}p${n}"
    [ -b "$node" ] && continue
    sysdev="/sys/class/block/${LOOP_BASE}p${n}/dev"
    for _ in 1 2 3 4 5; do [ -f "$sysdev" ] && break; sleep 0.3; done
    [ -f "$sysdev" ] && { mm="$(cat "$sysdev")"; mknod "$node" b "${mm%:*}" "${mm#*:}"; }
done
P_BOOT="${LOOP}p2"; P_A="${LOOP}p3"; P_B="${LOOP}p4"; P_OVL="${LOOP}p5"
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

log "Formatting filesystems"
mkfs.ext4 -q -L BOOT     "$P_BOOT"
mkfs.ext4 -q -L rootfs-a "$DEV_A"
mkfs.ext4 -q -L rootfs-b "$DEV_B"
mkfs.ext4 -q -L overlay  "$DEV_OVL"

log "Mounting root slot A"
mkdir -p "$MNT"
mount "$DEV_A" "$MNT"
mkdir -p "$BOOTMNT" "$MNT/var/lib/overlay"
mount "$P_BOOT" "$BOOTMNT"
mount "$DEV_OVL" "$MNT/var/lib/overlay"

log "Bootstrapping Debian $SUITE ($ARCH)"
debootstrap --arch="$ARCH" --variant=minbase \
    --include=systemd-sysv,ifupdown,netbase \
    "$SUITE" "$MNT" "$MIRROR"

log "Binding pseudo-filesystems for chroot"
mount --bind /dev "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
mount -t proc proc "$MNT/proc"
mount -t sysfs sys "$MNT/sys"

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
LABEL=overlay              /var/lib/overlay   ext4   defaults,nofail 0     2
tmpfs                      /tmp               tmpfs  defaults       0      0
EOF

cat > "$MNT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main contrib non-free-firmware
deb $MIRROR ${SUITE}-updates main contrib non-free-firmware
deb http://security.debian.org/debian-security ${SUITE}-security main contrib non-free-firmware
EOF

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
    cat > "$MNT/etc/crypttab" <<EOF
# <name>          <device>                 <keyfile>     <options>
luks-rootfs-a     UUID=$UUID_A             $KEYREF_A     luks,discard$NETOPT
luks-rootfs-b     UUID=$UUID_B             $KEYREF_B     luks,discard$NETOPT
luks-overlay      UUID=$UUID_OVL           $KEYREF_OVL   luks,discard$NETOPT
EOF
fi

log "Installing kernel, bootloader, and tooling in chroot"
cat > "$MNT/tmp/setup.sh" <<CHROOT
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    linux-image-${ARCH} grub-pc grub-pc-bin \
    openssh-server sudo ca-certificates \
    systemd-resolved cloud-guest-utils gdisk parted e2fsprogs \
    rauc ${CRYPT_PACKAGES} ${EXTRA_PACKAGES}
systemctl enable ssh systemd-networkd systemd-resolved

useradd -m -s /bin/bash -G sudo "${USERNAME}"
echo "${USERNAME}:${PASSWORD}" | chpasswd
passwd -l root
CHROOT
chroot "$MNT" bash /tmp/setup.sh
rm -f "$MNT/tmp/setup.sh"

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

log "Applying overlay files (RAUC, GRUB, first-boot expand, LUKS enroll)"
cp -a "$OVERLAY_DIR"/etc/. "$MNT/etc/"
cp -a "$OVERLAY_DIR"/usr/. "$MNT/usr/"
chmod +x "$MNT/usr/local/sbin/first-boot-expand.sh" "$MNT/usr/local/sbin/luks-enroll.sh"
chroot "$MNT" systemctl enable first-boot-expand.service

[ -f "$MNT/etc/rauc/keyring.pem" ] || cp "$MNT/etc/ssl/certs/ca-certificates.crt" "$MNT/etc/rauc/keyring.pem" 2>/dev/null || touch "$MNT/etc/rauc/keyring.pem"

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
    chroot "$MNT" update-initramfs -u
fi

log "Installing GRUB to $LOOP and writing A/B config"
chroot "$MNT" grub-install --target=i386-pc --boot-directory=/boot --recheck "$LOOP"
KVER="$(ls "$BOOTMNT" | sed -n 's/^vmlinuz-//p' | head -n1)"
[ -n "$KVER" ] || die "no kernel found on BOOT partition"
log "Kernel version: $KVER"
sed "s/__KVER__/$KVER/g" "$OVERLAY_DIR/boot/grub/grub.cfg" > "$BOOTMNT/grub/grub.cfg"
chroot "$MNT" grub-editenv /boot/grub/grubenv create
chroot "$MNT" grub-editenv /boot/grub/grubenv set ORDER="A B" A_TRY=0 B_TRY=0

log "Syncing root slot A -> slot B"
umount "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys"
umount "$MNT/var/lib/overlay"
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
    zstd) log "Compressing with zstd"; zstd -f -19 -T0 --rm "$RAW" -o "${RAW}.zst"; OUT="${RAW}.zst";;
    gzip) log "Compressing with gzip"; gzip -f "$RAW"; OUT="${RAW}.gz";;
    none) OUT="$RAW";;
    *) warn "Unknown compression '$COMPRESS', leaving raw"; OUT="$RAW";;
esac

log "Done: $OUT"
[ "$ENCRYPT" = true ] && log "Encryption: LUKS2, unlock=$UNLOCK (passphrase is also enrolled for recovery)"
ls -lh "$OUT"
