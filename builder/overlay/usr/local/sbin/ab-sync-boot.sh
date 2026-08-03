#!/bin/bash
# Copy this machine's current kernel and initramfs into the booted slot's
# directory on /boot.
#
# GRUB boots /boot/<A|B>/vmlinuz and /boot/<A|B>/initrd.img -- one pair per slot,
# so an update can replace the inactive slot's kernel without disturbing the one
# the running system depends on. Anything that regenerates the initramfs writes
# to the versioned files at the top of /boot instead, where dpkg and
# update-initramfs expect them, and GRUB never looks. Without this, a
# regenerated initramfs is simply ignored at the next boot.
#
# That matters most for LUKS enrolment: it re-runs update-initramfs so the
# initramfs can unlock via TPM or Tang, and if the slot's copy is stale the
# machine comes up asking for a passphrase nobody is there to type.
set -u

SLOT="$(sed -n 's/.*rauc\.slot=\([AB]\).*/\1/p' /proc/cmdline 2>/dev/null)"
[ -n "$SLOT" ] || { echo "ab-sync-boot: no rauc.slot on the command line" >&2; exit 1; }

KVER="${1:-$(uname -r)}"
SRC_K="/boot/vmlinuz-${KVER}"
SRC_I="/boot/initrd.img-${KVER}"
[ -f "$SRC_K" ] || { echo "ab-sync-boot: no $SRC_K" >&2; exit 1; }
[ -f "$SRC_I" ] || { echo "ab-sync-boot: no $SRC_I" >&2; exit 1; }

mkdir -p "/boot/${SLOT}"
# Written alongside and renamed into place: a half-copied kernel in the slot's
# directory is a machine that does not boot, and this runs on a live system
# where losing power mid-copy is a real possibility.
cp -f "$SRC_K" "/boot/${SLOT}/.vmlinuz.new"
cp -f "$SRC_I" "/boot/${SLOT}/.initrd.img.new"
sync
mv -f "/boot/${SLOT}/.vmlinuz.new"    "/boot/${SLOT}/vmlinuz"
mv -f "/boot/${SLOT}/.initrd.img.new" "/boot/${SLOT}/initrd.img"
sync
echo "ab-sync-boot: slot ${SLOT} now boots kernel ${KVER}"
