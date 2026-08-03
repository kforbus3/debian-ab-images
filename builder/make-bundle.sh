#!/bin/bash
#
# Turn a built A/B image into a signed RAUC update bundle.
#
# This is the piece that makes the two slots worth having. Without it the only
# way to patch a machine is to re-image it, which is what the A/B layout exists
# to avoid: an update writes the *inactive* slot while the machine keeps running
# on the active one, flips the boot order, and reboots. If the new slot does not
# come up, GRUB's try-counter falls back to the old one on its own.
#
# What goes in the bundle is the root slot only. /boot, the ESP and the overlay
# are deliberately left alone: the overlay is the machine's data and identity,
# and destroying it on update is the bug this project already fixed once.
#
#   ./make-bundle.sh --image /output/debian-trixie-ab.img [--version 2026.08.03]
#
# Runs inside the builder container (it needs rauc, loop devices, and root).
set -euo pipefail

IMAGE=""
VERSION=""
OUTPUT=""
COMPATIBLE=""
KEYDIR="${KEYDIR:-/output/rauc-keys}"
DESCRIPTION=""

log()  { echo -e "\033[0;32m[bundle]\033[0m $*"; }
die()  { echo -e "\033[0;31m[bundle] ERROR:\033[0m $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --image)       IMAGE="$2"; shift 2;;
        --version)     VERSION="$2"; shift 2;;
        --output)      OUTPUT="$2"; shift 2;;
        --compatible)  COMPATIBLE="$2"; shift 2;;
        --description) DESCRIPTION="$2"; shift 2;;
        --keydir)      KEYDIR="$2"; shift 2;;
        -h|--help) sed -n '2,20p' "$0"; exit 0;;
        *) die "unknown option '$1'";;
    esac
done

[ -n "$IMAGE" ] || die "--image is required"
[ -f "$IMAGE" ] || die "no such image: $IMAGE"
VERSION="${VERSION:-$(date -u +%Y.%m.%d-%H%M)}"

# --- signing key -------------------------------------------------------------
#
# Generated once and kept, because the certificate has to be inside every image
# that will ever accept a bundle signed by it -- rotating the key orphans every
# machine already deployed. build-image.sh installs $KEYDIR/cert.pem into the
# image's keyring, so keys must exist before the images that trust them.
mkdir -p "$KEYDIR"
if [ ! -f "$KEYDIR/key.pem" ] || [ ! -f "$KEYDIR/cert.pem" ]; then
    log "Generating a signing key in $KEYDIR (keep it: images trust this cert)"
    openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
        -keyout "$KEYDIR/key.pem" -out "$KEYDIR/cert.pem" \
        -subj "/O=debian-ab-images/CN=A-B Update Signing" 2>/dev/null
    chmod 600 "$KEYDIR/key.pem"
fi

WORK="$(mktemp -d)"
cleanup() {
    mountpoint -q "$WORK/slot" 2>/dev/null && umount "$WORK/slot" || true
    [ -n "${LOOP:-}" ] && losetup -d "$LOOP" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

log "Reading root slot A from $(basename "$IMAGE")"
LOOP="$(losetup -f --show -P "$IMAGE")"
# udev does not run in the builder container, so the partition nodes the kernel
# knows about have to be created by hand from sysfs.
BB="$(basename "$LOOP")"
for n in $(ls /sys/class/block/ 2>/dev/null | sed -n "s/^${BB}p//p" | sort -n); do
    IFS=: read -r mj mn < "/sys/class/block/${BB}p${n}/dev"
    rm -f "/dev/${BB}p${n}"; mknod "/dev/${BB}p${n}" b "$mj" "$mn"
done

SLOT_PART=""
for n in $(ls /sys/class/block/ 2>/dev/null | sed -n "s/^${BB}p//p" | sort -n); do
    dev="/dev/${BB}p${n}"
    if cryptsetup isLuks "$dev" 2>/dev/null; then
        die "this image is encrypted; bundling an encrypted slot is not supported yet.
  Build the image without --encrypt to produce update bundles from it, or take
  the bundle from an unencrypted build of the same release."
    fi
    if [ "$(sgdisk -i "$n" "$LOOP" 2>/dev/null | sed -n 's/^Partition name: .\(.*\).$/\1/p')" = "rootfs-a" ]; then
        SLOT_PART="$dev"; break
    fi
done
[ -n "$SLOT_PART" ] || die "could not find the rootfs-a partition in $IMAGE"

mkdir -p "$WORK/slot" "$WORK/bundle"
mount -o ro "$SLOT_PART" "$WORK/slot" || die "could not mount the root slot"

if [ -z "$COMPATIBLE" ]; then
    COMPATIBLE="$(sed -n 's/^compatible=//p' "$WORK/slot/etc/rauc/system.conf" 2>/dev/null | head -1)"
fi
[ -n "$COMPATIBLE" ] || die "could not read 'compatible' from the image's rauc/system.conf"
log "compatible=$COMPATIBLE  version=$VERSION"

# RAUC verifies the bundle against the running system's `compatible`, which is
# what stops a Debian bundle being installed onto an Ubuntu machine.
# A tar, not a filesystem image. RAUC picks its update handler from the image's
# file extension and the slot type: for an ext4 slot a .tar* is "make a fresh
# filesystem and extract into it", which is what an A/B root update should be.
# A squashfs is rejected outright -- "Unsupported image rootfs.squashfs for slot
# type ext4" -- and a raw ext4 image would tie the bundle to one slot size.
#
# --numeric-owner because the bundle is unpacked on a machine whose passwd file
# is not consulted; --xattrs to carry capabilities (ping, etc.) across.
#
# squashfs-tools is still required in the builder even though the payload is a
# tar: rauc builds the bundle container itself with mksquashfs.
log "Packing the root slot (this is the slow part)"
tar --numeric-owner --xattrs --xattrs-include='*' \
    --warning=no-file-ignored --warning=no-file-changed \
    -C "$WORK/slot" -czf "$WORK/bundle/rootfs.tar.gz" . 

# RAUC makes a fresh filesystem in the target slot, and a fresh ext4 has no
# label. GRUB boots by root=LABEL=rootfs-a|b, so without this the updated slot
# comes up as "ALERT! LABEL=rootfs-b does not exist" and drops to an initramfs
# shell -- the update installs perfectly and then bricks the boot.
#
# A post-install hook rather than extra-mkfs-opts in system.conf: the hook works
# on every RAUC version, and it runs on the machine, so it labels whichever slot
# was actually written rather than whatever the bundle guessed.
cat > "$WORK/bundle/hook.sh" <<'HOOK'
#!/bin/sh
set -e
case "$1" in
    slot-post-install)
        case "$RAUC_SLOT_BOOTNAME" in
            A) label=rootfs-a;;
            B) label=rootfs-b;;
            *) exit 0;;
        esac
        e2label "$RAUC_SLOT_DEVICE" "$label"
        ;;
esac
exit 0
HOOK
chmod +x "$WORK/bundle/hook.sh"

cat > "$WORK/bundle/manifest.raucm" <<EOF
[bundle]
# verity, not plain. RAUC installs straight from an HTTP URL by streaming, and
# streaming refuses a plain bundle outright -- "Bundle format 'plain' not
# supported in streaming mode" -- so a plain bundle can only be installed from a
# file already on the machine. verity has been supported since RAUC 1.5, which
# covers every suite this builder targets. ab-update falls back to downloading
# first if a machine ever does refuse to stream.
format=verity

[update]
compatible=${COMPATIBLE}
version=${VERSION}
description=${DESCRIPTION:-A/B root filesystem update}

[hooks]
filename=hook.sh

[image.rootfs]
filename=rootfs.tar.gz
hooks=post-install
EOF

umount "$WORK/slot"

OUTPUT="${OUTPUT:-/output/bundles/${COMPATIBLE}-${VERSION}.raucb}"
mkdir -p "$(dirname "$OUTPUT")"
rm -f "$OUTPUT"

# Built into the container's own filesystem and copied out afterwards, rather
# than written straight to /output. RAUC refuses to read a 'plain' bundle from a
# filesystem it does not recognise -- a guard against the bundle changing under
# it mid-install -- and /output is a bind mount, which on some hosts (Docker
# Desktop's virtiofs, for one) is exactly that. Verification would fail there on
# a bundle that is perfectly good.
STAGED="$WORK/staged.raucb"
log "Signing the bundle"
rauc bundle --cert="$KEYDIR/cert.pem" --key="$KEYDIR/key.pem" \
    "$WORK/bundle" "$STAGED"

# Verify against the certificate machines will carry, rather than trusting that
# signing succeeded. This is the check RAUC performs on the machine itself, so a
# bundle failing here would have failed there.
log "Verifying the bundle against the keyring machines will use"
rauc info --keyring="$KEYDIR/cert.pem" "$STAGED" >/dev/null 2>&1 \
    || die "the finished bundle does not verify against $KEYDIR/cert.pem"

mv "$STAGED" "$OUTPUT"

# The sidecar is what the web UI reads to list bundles without unpacking them.
cat > "${OUTPUT}.json" <<EOF
{"version":"${VERSION}","compatible":"${COMPATIBLE}","source":"$(basename "$IMAGE")",
 "created":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","description":"${DESCRIPTION:-A/B root filesystem update}"}
EOF
sha256sum "$OUTPUT" | awk '{print $1}' > "${OUTPUT}.sha256"

# A pointer to the newest bundle, so `ab-update` with no arguments has something
# to ask for. Directory listing is off on the HTTP server (and parsing an index
# would be a poor contract anyway), so the newest build states it explicitly.
basename "$OUTPUT" > "$(dirname "$OUTPUT")/latest"

log "Bundle built: $OUTPUT"
ls -lh "$OUTPUT" | awk '{print "  " $9 "  " $5}'
log "Install it on a machine with:"
echo "    rauc install http://<server>/bundles/$(basename "$OUTPUT")"
