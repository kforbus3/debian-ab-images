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

# Every failure below exits non-zero. It used to exit 0 on all of them, which
# meant `systemctl status ab-mark-good` said "success" on a machine whose try
# counter was still armed -- so the one place anybody would look to explain a
# spontaneous slot switch actively said there was nothing wrong. A machine one
# reboot away from silently changing slots must not look healthy.
if [ -z "$SLOT" ]; then
    echo "ab-mark-good: cannot determine booted slot from /proc/cmdline" >&2
    echo "ab-mark-good: the try counter is still armed; the next boot will use the other slot" >&2
    exit 1
fi
if [ ! -s "$GRUBENV" ]; then
    echo "ab-mark-good: $GRUBENV missing; is /boot mounted?" >&2
    echo "ab-mark-good: the try counter is still armed; the next boot will use the other slot" >&2
    exit 1
fi

# _OK as well as _TRY: the try counter is what GRUB falls back on, and _OK is
# what RAUC reads to decide whether a slot is usable at all. Setting only the
# counter leaves RAUC convinced every slot is bad.
if ! grub-editenv "$GRUBENV" set "${SLOT}_TRY=0" "${SLOT}_OK=1"; then
    echo "ab-mark-good: could not write $GRUBENV (is /boot read-only?)" >&2
    echo "ab-mark-good: the try counter is still armed; the next boot will use the other slot" >&2
    exit 1
fi

# Read it back rather than trusting the write. grub-editenv writes a temporary
# file and renames it into place, so a full or read-only /boot can fail in ways
# that still exit 0 -- and the symptom of believing a failed write is a machine
# that changes slots by itself on the next reboot, which is not a thing anyone
# traces back to here.
if [ "$(grub-editenv "$GRUBENV" list 2>/dev/null | sed -n "s/^${SLOT}_TRY=//p")" != "0" ]; then
    echo "ab-mark-good: ${SLOT}_TRY did not stick after writing $GRUBENV" >&2
    echo "ab-mark-good: the try counter is still armed; the next boot will use the other slot" >&2
    exit 1
fi
echo "ab-mark-good: slot $SLOT marked good (${SLOT}_TRY=0)"
