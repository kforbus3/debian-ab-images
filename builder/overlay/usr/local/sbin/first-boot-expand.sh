#!/bin/bash
# Grow the overlay (last) partition and its filesystem to fill the disk on first
# boot. Best-effort and idempotent: it never fails the boot, and disables itself
# once done. When the disk has no free space (e.g. an image-sized VM), it simply
# records completion and exits.
set -u

STAMP=/var/lib/first-boot-expand.done
[ -f "$STAMP" ] && exit 0

OVERLAY_PART="$(blkid -L overlay 2>/dev/null || true)"
if [ -n "$OVERLAY_PART" ]; then
    DISK="/dev/$(lsblk -ndo PKNAME "$OVERLAY_PART" 2>/dev/null || true)"
    PARTNUM="$(echo "$OVERLAY_PART" | grep -oE '[0-9]+$' || true)"
    if [ -b "$DISK" ] && [ -n "$PARTNUM" ]; then
        echo "first-boot-expand: growing ${OVERLAY_PART} on ${DISK} (partition ${PARTNUM})"
        if growpart "$DISK" "$PARTNUM" 2>/dev/null; then
            resize2fs "$OVERLAY_PART" 2>/dev/null || true
            echo "first-boot-expand: grown"
        else
            echo "first-boot-expand: no free space to grow (nothing to do)"
        fi
    fi
fi

touch "$STAMP"
systemctl disable first-boot-expand.service >/dev/null 2>&1 || true
exit 0
