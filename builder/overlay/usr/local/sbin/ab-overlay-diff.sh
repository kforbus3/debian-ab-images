#!/bin/bash
# What has changed on this machine since it was imaged, and what that is hiding.
#
# The root filesystem is an overlay: the read-only lower layer is the A/B root
# slot exactly as the imager wrote it, and every write since lands in the upper
# layer on the overlay partition. The upper layer is deliberately shared by both
# slots, so an A/B update replaces the OS without destroying /home.
#
# The cost of that is invisible from inside the running system: a file you edit
# in slot A shadows slot B's copy of the same file, including one an update was
# meant to deliver. Nothing in `ls` or `cat` says which layer you are looking at.
# This prints that.
set -u

OVL=/var/lib/overlay          # the overlay partition
UPPER="$OVL/upper"
LOWER=""                      # the booted slot, read-only; found below

BOLD=""; DIM=""; RED=""; YEL=""; GRN=""; OFF=""
if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; YEL=$'\033[33m'
    GRN=$'\033[32m'; OFF=$'\033[0m'
fi

usage() {
    cat <<EOF
Usage: ab-overlay-diff [options]

Lists what this machine has written since it was imaged, and which of those
files are shadowing a file from the image.

  -a, --all        every changed path, not just the ones shadowing the image
  -q, --quiet      counts only, no file list
  -h, --help       this text

Recovery, when a change here is the problem:

  Reboot and choose "Recovery: Slot <A|B>, reset overlay" from the GRUB menu.
  The upper layer is renamed to $OVL/upper.prev -- nothing is deleted -- and
  the machine boots with a clean one. Copy anything you need back out of it.

  "Recovery: Slot <A|B>, no overlay" boots the root slot exactly as imaged,
  ignoring the overlay entirely. Use it to confirm whether a problem lives in
  the image or in the changes on top of it.
EOF
}

ALL=0; QUIET=0
while [ $# -gt 0 ]; do
    case "$1" in
        -a|--all)   ALL=1;;
        -q|--quiet) QUIET=1;;
        -h|--help)  usage; exit 0;;
        *) echo "ab-overlay-diff: unknown option '$1'" >&2; usage >&2; exit 2;;
    esac
    shift
done

if ! findmnt -no FSTYPE / 2>/dev/null | grep -qx overlay; then
    echo "${YEL}This machine is not running with an overlay root.${OFF}"
    echo "Either it booted with ab.overlay=off, or the overlay could not be set"
    echo "up (dmesg | grep ab-overlay says which). Nothing to compare."
    exit 1
fi
if [ ! -d "$UPPER" ]; then
    echo "ab-overlay-diff: no upper layer at $UPPER" >&2
    exit 1
fi

SLOT="$(sed -n 's/.*rauc\.slot=\([AB]\).*/\1/p' /proc/cmdline 2>/dev/null)"

# The lower layer has to be reached on purpose. The initramfs binds it inside
# the new root before switching, but systemd mounts a fresh tmpfs over /run
# immediately afterwards, so anything left there is gone by the time a person
# could look at it. Mounting the slot read-only here is independent of all that:
# it is the same filesystem the overlay is stacked on, and read-only means this
# cannot disturb the running system or the slot.
TMPLOWER=""
cleanup() {
    [ -n "$TMPLOWER" ] || return 0
    umount "$TMPLOWER" 2>/dev/null || true
    rmdir "$TMPLOWER" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if mountpoint -q /run/ab-lower 2>/dev/null; then
    LOWER=/run/ab-lower
else
    case "${SLOT:-}" in
        A) DEV=/dev/mapper/luks-rootfs-a; [ -b "$DEV" ] || DEV="$(blkid -L rootfs-a 2>/dev/null)";;
        B) DEV=/dev/mapper/luks-rootfs-b; [ -b "$DEV" ] || DEV="$(blkid -L rootfs-b 2>/dev/null)";;
        *) DEV="";;
    esac
    if [ -n "$DEV" ] && [ -b "$DEV" ]; then
        TMPLOWER="$(mktemp -d /tmp/ab-lower.XXXXXX)"
        # Deliberately not -o ro. Reaching this point means root is an overlay,
        # which means this filesystem is already mounted read-write as its lower
        # layer, and ext4 refuses a second mount of one superblock with a
        # different RO state -- it fails and logs "would change RO state" to the
        # console every time, which is alarming for a read-only inspection tool.
        # Matching the existing flags adds no exposure: it is the same superblock
        # the running system already has mounted, and nothing here writes to it.
        if mount "$DEV" "$TMPLOWER" 2>/dev/null; then
            LOWER="$TMPLOWER"
        else
            rmdir "$TMPLOWER" 2>/dev/null || true; TMPLOWER=""
        fi
    fi
fi
echo "${BOLD}Machine changes since imaging${OFF}${DIM}  (slot ${SLOT:-?}, upper layer $UPPER)${OFF}"
echo ""

# An overlayfs whiteout is a character device 0/0 in the upper layer: the file
# exists in the image and has been deleted here. Worth separating, because a
# deletion that hides a file the image still needs looks like nothing at all.
shadowing=0; added=0; deleted=0
list_shadow=""; list_add=""; list_del=""

while IFS= read -r path; do
    rel="${path#$UPPER}"
    [ "$rel" = "$path" ] && continue
    [ -z "$rel" ] && continue
    case "$rel" in /work|/work/*) continue;; esac

    if [ -c "$path" ] && [ "$(stat -c '%t:%T' "$path" 2>/dev/null)" = "0:0" ]; then
        deleted=$((deleted + 1))
        list_del="${list_del}${rel}"$'\n'
    elif [ -n "$LOWER" ] && [ -e "$LOWER$rel" ]; then
        shadowing=$((shadowing + 1))
        list_shadow="${list_shadow}${rel}"$'\n'
    else
        added=$((added + 1))
        list_add="${list_add}${rel}"$'\n'
    fi
done < <(find "$UPPER" \( -type f -o -type l -o -type c \) 2>/dev/null)

if [ -z "$LOWER" ]; then
    echo "${YEL}Note:${OFF} the root slot for this machine could not be mounted, so"
    echo "nothing can be compared against the image and every change below is"
    echo "reported as added rather than as shadowing."
    echo ""
fi

show() {  # show <title> <colour> <list>
    [ "$QUIET" = 1 ] && return 0
    [ -z "$3" ] && return 0
    echo "${2}${1}${OFF}"
    printf '%s' "$3" | sed '/^$/d' | sort | sed 's/^/  /'
    echo ""
}

show "Shadowing a file from the image ($shadowing)" "$RED" "$list_shadow"
if [ "$ALL" = 1 ]; then
    show "Added by this machine ($added)" "$GRN" "$list_add"
    show "Deleted from the image ($deleted)" "$YEL" "$list_del"
fi

echo "${BOLD}${shadowing}${OFF} shadowing the image, ${BOLD}${added}${OFF} added, ${BOLD}${deleted}${OFF} deleted"
[ "$ALL" = 0 ] && [ $((added + deleted)) -gt 0 ] && \
    echo "${DIM}(-a lists the added and deleted ones too)${OFF}"

if [ "$shadowing" -gt 0 ]; then
    echo ""
    echo "The files above take precedence over the image on ${BOLD}both${OFF} slots, so an"
    echo "A/B update cannot replace them and booting the other slot will not"
    echo "leave them behind. If one of them is the problem, reboot and choose"
    echo "${BOLD}Recovery: Slot <A|B>, reset overlay${OFF} from the GRUB menu -- the upper"
    echo "layer is kept as ${BOLD}$OVL/upper.prev${OFF}, nothing is deleted."
fi
