#!/bin/bash
#
# Build the netboot imager: a kernel + initramfs that auto-images a machine's
# local disk from an HTTP image URL.
#
# amd64 goes to /output/imager/{vmlinuz,initramfs.img} and every other
# architecture to /output/imager/<arch>/. amd64 keeps the top-level path it has
# always had so that servers and images already deployed keep working; nothing
# has to be rebuilt or moved to gain arm64 support. boot.ipxe picks the right
# directory from iPXE's own ${buildarch}, so the machine chooses, not the config.
#
# Runs inside the imager builder container (see Dockerfile).
set -euo pipefail

ARCH="${ARCH:-amd64}"
case "$ARCH" in
    amd64) OUT="${OUT:-/output/imager}";;
    arm64) OUT="${OUT:-/output/imager/arm64}";;
    *) echo "[imager-build] unsupported ARCH '$ARCH' (expected amd64 or arm64)" >&2; exit 2;;
esac
HERE="$(cd "$(dirname "$0")" && pwd)"

log() { echo -e "\033[0;32m[imager-build]\033[0m $*"; }

mkdir -p "$OUT"
WORK="$(mktemp -d)"
ROOT="$WORK/initrd"
mkdir -p "$ROOT"/{bin,sbin,etc,proc,sys,dev,tmp,newroot,usr/bin,usr/sbin,usr/share/udhcpc,lib,lib64}

KVER="$(ls /lib/modules | sort -V | tail -n1)"
log "Kernel version: $KVER"

# --- Kernel ---
cp "/boot/vmlinuz-${KVER}" "$OUT/vmlinuz"

# --- Busybox (provides sh, wget, gzip, ip, udhcpc, dd, mknod, etc.) ---
cp /bin/busybox "$ROOT/bin/busybox"
ln -sf busybox "$ROOT/bin/sh"

# --- Real zstd binary (busybox has no zstd) plus its shared libraries ---
copy_with_libs() {
    local bin="$1" dst="$2"
    cp "$bin" "$ROOT/$dst/"
    ldd "$bin" 2>/dev/null | grep -oE '/[^ ]+\.so[^ ]*' | while read -r lib; do
        local d="$ROOT$(dirname "$lib")"
        mkdir -p "$d"; cp -L "$lib" "$d/" 2>/dev/null || true
    done
}
copy_with_libs "$(command -v zstd)" usr/bin
# sgdisk relocates the GPT backup header after the image lands on a disk that is
# bigger than the image (see init). busybox has nothing that can do this.
copy_with_libs "$(command -v sgdisk)" usr/sbin
# The dynamic loader itself.
for ldso in /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-aarch64.so.1 /lib/ld-linux.so.2; do
    [ -f "$ldso" ] && { mkdir -p "$ROOT$(dirname "$ldso")"; cp -L "$ldso" "$ROOT$ldso"; }
done

# --- Kernel modules ---
# Include the entire module tree so dependency resolution always succeeds across
# arbitrary hardware (NICs, storage controllers). The initramfs is loaded once
# into RAM at boot, so a larger tree only costs a one-time transfer.
MODSRC="/lib/modules/$KVER"
mkdir -p "$ROOT/lib/modules/$KVER"
cp -a "$MODSRC"/. "$ROOT/lib/modules/$KVER/"
# Debian ships kernel modules compressed (.ko.xz / .ko.zst). busybox modprobe
# cannot decompress them, so expand them in place, then regenerate dependencies.
find "$ROOT/lib/modules/$KVER" -name '*.ko.xz'  -exec unxz {} + 2>/dev/null || true
find "$ROOT/lib/modules/$KVER" -name '*.ko.zst' -exec zstd -d --rm {} + 2>/dev/null || true
depmod -b "$ROOT" "$KVER" 2>/dev/null || true

# --- udhcpc callback + init ---
cp "$HERE/udhcpc.script" "$ROOT/usr/share/udhcpc/default.script"
chmod +x "$ROOT/usr/share/udhcpc/default.script"
cp "$HERE/init" "$ROOT/init"
chmod +x "$ROOT/init"

# --- Pack the initramfs ---
log "Packing initramfs"
( cd "$ROOT" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$OUT/initramfs.img"

echo "$KVER" > "$OUT/KERNEL_VERSION"

# --- what this imager understands ---------------------------------------------
#
# The imager is a build artifact: `init` is baked into the initramfs, so a repo
# that has grown a new imager.* parameter does nothing until someone runs
# `make imager`. Nothing said so, and the failure is silent in the worst way --
# the iPXE script passes imager.hostname=, an older imager ignores unknown
# parameters exactly as it should, and the machine images perfectly with the
# wrong name. It looks like the web UI dropped the field.
#
# So the imager states what it supports, and the web UI reads it back and warns
# when an assignment needs something this build does not have. Derived from
# `init` itself rather than hand-maintained: a list that has to be remembered is
# a list that goes stale exactly when it matters.
FEATURES="$(grep -oE 'getarg imager\.[a-z]+' "$HERE/init" \
            | sed 's/getarg imager\.//' | sort -u | tr '\n' ' ')"
printf '{"built":"%s","kernel":"%s","features":[%s]}\n' \
    "$(date -u +%FT%TZ)" "$KVER" \
    "$(printf '%s' "$FEATURES" | sed 's/ *$//; s/[^ ][^ ]*/"&"/g; s/ /,/g')" \
    > "$OUT/build.json"
log "Imager features: ${FEATURES:-none detected}"
rm -rf "$WORK"

log "Imager built:"
ls -lh "$OUT/vmlinuz" "$OUT/initramfs.img"
