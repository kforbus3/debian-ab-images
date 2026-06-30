#!/bin/bash
# Grow the overlay (last) partition and its filesystem to fill the disk on first
# boot. Handles both plain and LUKS-encrypted overlays. Best-effort and
# idempotent: it never fails the boot, and disables itself once done.
set -u

STAMP=/var/lib/first-boot-expand.done
[ -f "$STAMP" ] && exit 0

OVERLAY_FS="$(blkid -L overlay 2>/dev/null || true)"   # ext4 device (mapper if encrypted)
if [ -n "$OVERLAY_FS" ]; then
    # Walk down to the backing partition (mapper -> partition).
    PARENT="/dev/$(lsblk -ndo PKNAME "$OVERLAY_FS" 2>/dev/null || true)"
    ENCRYPTED=0
    case "$OVERLAY_FS" in /dev/mapper/*) ENCRYPTED=1;; esac

    if [ "$ENCRYPTED" = 1 ]; then
        CRYPT_PART="$PARENT"                                  # the LUKS partition
        DISK="/dev/$(lsblk -ndo PKNAME "$CRYPT_PART" 2>/dev/null || true)"
        PARTNUM="$(echo "$CRYPT_PART" | grep -oE '[0-9]+$' || true)"
        MAPNAME="$(basename "$OVERLAY_FS")"
        if [ -b "$DISK" ] && [ -n "$PARTNUM" ]; then
            echo "first-boot-expand: growing encrypted overlay on ${DISK} (part ${PARTNUM})"
            if growpart "$DISK" "$PARTNUM" 2>/dev/null; then
                cryptsetup resize "$MAPNAME" 2>/dev/null || true
                resize2fs "$OVERLAY_FS" 2>/dev/null || true
                echo "first-boot-expand: grown (encrypted)"
            else
                echo "first-boot-expand: no free space to grow"
            fi
        fi
    else
        DISK="$PARENT"
        PARTNUM="$(echo "$OVERLAY_FS" | grep -oE '[0-9]+$' || true)"
        if [ -b "$DISK" ] && [ -n "$PARTNUM" ]; then
            echo "first-boot-expand: growing overlay on ${DISK} (part ${PARTNUM})"
            if growpart "$DISK" "$PARTNUM" 2>/dev/null; then
                resize2fs "$OVERLAY_FS" 2>/dev/null || true
                echo "first-boot-expand: grown"
            else
                echo "first-boot-expand: no free space to grow"
            fi
        fi
    fi
fi

touch "$STAMP"
systemctl disable first-boot-expand.service >/dev/null 2>&1 || true
exit 0
