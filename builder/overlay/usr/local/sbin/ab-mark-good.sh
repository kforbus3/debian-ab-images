#!/bin/bash
# Mark the booted A/B slot as good by resetting its GRUB try counter.
#
# grub.cfg sets <SLOT>_TRY=1 when it picks a slot; if nothing resets it, the
# next boot skips this slot and falls through to the other one. Running this
# once the system reaches multi-user restores the counter, which is exactly
# what RAUC's "rauc status mark-good" would do — but without depending on the
# RAUC daemon being configured.
set -u

GRUBENV=/boot/grub/grubenv

# The booted slot comes from the kernel command line (rauc.slot=A|B), with the
# root= label as a fallback.
SLOT=""
for arg in $(cat /proc/cmdline); do
    case "$arg" in
        rauc.slot=A|rauc.slot=B) SLOT="${arg#rauc.slot=}";;
        root=LABEL=rootfs-a) : "${SLOT:=A}";;
        root=LABEL=rootfs-b) : "${SLOT:=B}";;
    esac
done

if [ -z "$SLOT" ]; then
    echo "ab-mark-good: cannot determine booted slot from /proc/cmdline" >&2
    exit 0
fi
if [ ! -s "$GRUBENV" ]; then
    echo "ab-mark-good: $GRUBENV missing; is /boot mounted?" >&2
    exit 0
fi

# _OK as well as _TRY: the try counter is what GRUB falls back on, and _OK is
# what RAUC reads to decide whether a slot is usable at all. Setting only the
# counter leaves RAUC convinced every slot is bad.
grub-editenv "$GRUBENV" set "${SLOT}_TRY=0" "${SLOT}_OK=1"
echo "ab-mark-good: slot $SLOT marked good (${SLOT}_TRY=0)"
