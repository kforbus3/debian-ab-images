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

# --- Resolve distro (auto-detect from suite when not given) ---
if [ -z "$DISTRO" ]; then
    case "$SUITE" in
        bionic|focal|jammy|noble|oracular|plucky|questing|resolute) DISTRO=ubuntu;;
        *) DISTRO=debian;;
    esac
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
OS_PRETTY="$(tr '[:lower:]' '[:upper:]' <<< "${DISTRO:0:1}")${DISTRO:1}"
HOSTNAME_="${HOSTNAME_:-${DISTRO}-ab}"
OUTPUT="${OUTPUT:-/output/${DISTRO}-${SUITE}-ab.img}"

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
    for m in dev proc sys boot/efi boot var/lib/overlay; do
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

log "Partitioning (GPT, hybrid BIOS+UEFI, A/B)"
parted -s "$RAW" mklabel gpt
parted -s "$RAW" mkpart bios     1MiB ${E_START}MiB
parted -s "$RAW" set 1 bios_grub on
parted -s "$RAW" mkpart ESP      fat32 ${E_START}MiB ${E_END}MiB
parted -s "$RAW" set 2 esp on
parted -s "$RAW" mkpart BOOT     ext4 ${E_END}MiB    ${B_END}MiB
parted -s "$RAW" mkpart rootfs-a ext4 ${B_END}MiB    ${A_END}MiB
parted -s "$RAW" mkpart rootfs-b ext4 ${A_END}MiB    ${BB_END}MiB
parted -s "$RAW" mkpart overlay  ext4 ${BB_END}MiB   100%

LOOP="$(losetup -f --show -P "$RAW")"
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

log "Formatting filesystems"
mkfs.vfat -F32 -n EFI    "$P_ESP" >/dev/null
mkfs.ext4 -q -L BOOT     "$P_BOOT"
mkfs.ext4 -q -L rootfs-a "$DEV_A"
mkfs.ext4 -q -L rootfs-b "$DEV_B"
mkfs.ext4 -q -L overlay  "$DEV_OVL"

log "Mounting root slot A"
mkdir -p "$MNT"
mount "$DEV_A" "$MNT"
mkdir -p "$BOOTMNT" "$MNT/var/lib/overlay"
mount "$P_BOOT" "$BOOTMNT"
mkdir -p "$BOOTMNT/efi"
mount "$P_ESP" "$BOOTMNT/efi"
mount "$DEV_OVL" "$MNT/var/lib/overlay"

log "Bootstrapping $OS_PRETTY $SUITE ($ARCH)"
debootstrap --arch="$ARCH" --variant=minbase $DEBOOTSTRAP_OPTS \
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
LABEL=EFI                  /boot/efi          vfat   umask=0077     0      1
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
# initramfs-tools is explicit: Debian kernels depend on it, Ubuntu kernels only
# recommend it, and without it no initrd.img is generated for GRUB to load.
apt-get install -y --no-install-recommends \
    ${KERNEL_PKG} initramfs-tools grub-pc grub-pc-bin grub-efi-amd64-bin \
    openssh-server sudo ca-certificates \
    ${RESOLVED_PKG} cloud-guest-utils gdisk parted e2fsprogs \
    rauc ${CRYPT_PACKAGES} ${EXTRA_PACKAGES}
systemctl enable ssh systemd-networkd systemd-resolved

useradd -m -s /bin/bash -G sudo "${USERNAME}"
echo "${USERNAME}:${PASSWORD}" | chpasswd
passwd -l root
CHROOT
chroot "$MNT" bash /tmp/setup.sh
rm -f "$MNT/tmp/setup.sh"

# Every machine imaged from this build must get its own identity. Blank the
# machine-id and drop the build-time SSH host keys; machine-identity.service
# regenerates them on first boot and persists them in the overlay so they
# survive A/B slot switches and updates.
log "Resetting machine identity (machine-id, SSH host keys)"
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

log "Applying overlay files (RAUC, GRUB, first-boot expand, LUKS enroll)"
cp -a "$OVERLAY_DIR"/etc/. "$MNT/etc/"
cp -a "$OVERLAY_DIR"/usr/. "$MNT/usr/"
# RAUC bundles are only accepted by systems with a matching compatible string.
sed -i "s/^compatible=.*/compatible=${DISTRO}-ab/" "$MNT/etc/rauc/system.conf"
chmod +x "$MNT/usr/local/sbin/first-boot-expand.sh" "$MNT/usr/local/sbin/luks-enroll.sh" \
         "$MNT/usr/local/sbin/ab-mark-good.sh" "$MNT/usr/local/sbin/machine-identity.sh"
chroot "$MNT" systemctl enable first-boot-expand.service ab-mark-good.service machine-identity.service

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

log "Installing GRUB (BIOS + UEFI) and writing A/B config"
chroot "$MNT" grub-install --target=i386-pc --boot-directory=/boot --recheck "$LOOP"
# --removable puts GRUB at the fallback path (\EFI\BOOT\BOOTX64.EFI) so any
# UEFI firmware boots it without an NVRAM entry — required for mass imaging,
# where NVRAM can't be prepared per machine. Secure Boot must be disabled.
chroot "$MNT" grub-install --target=x86_64-efi --efi-directory=/boot/efi \
    --boot-directory=/boot --removable --no-nvram
KVER="$(ls "$BOOTMNT" | sed -n 's/^vmlinuz-//p' | head -n1)"
[ -n "$KVER" ] || die "no kernel found on BOOT partition"
log "Kernel version: $KVER"
sed -e "s/__KVER__/$KVER/g" -e "s/__OS__/$OS_PRETTY/g" \
    "$OVERLAY_DIR/boot/grub/grub.cfg" > "$BOOTMNT/grub/grub.cfg"
chroot "$MNT" grub-editenv /boot/grub/grubenv create
chroot "$MNT" grub-editenv /boot/grub/grubenv set ORDER="A B" A_TRY=0 B_TRY=0

log "Syncing root slot A -> slot B"
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
    zstd) log "Compressing with zstd"; zstd -f -19 -T0 --rm "$RAW" -o "${RAW}.zst"; OUT="${RAW}.zst";;
    gzip) log "Compressing with gzip"; gzip -f "$RAW"; OUT="${RAW}.gz";;
    none) OUT="$RAW";;
    *) warn "Unknown compression '$COMPRESS', leaving raw"; OUT="$RAW";;
esac

log "Writing SHA256 checksum and metadata sidecars"
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

log "Done: $OUT"
[ "$ENCRYPT" = true ] && log "Encryption: LUKS2, unlock=$UNLOCK (passphrase is also enrolled for recovery)"
ls -lh "$OUT" "${OUT}.sha256" "${OUT}.json"
