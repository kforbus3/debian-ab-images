#!/bin/bash
# First-boot LUKS enrollment, phase 2 of 2: destroy the bootstrap key.
#
# luks-enroll.sh bound the volumes to the TPM (or Tang) and rebuilt the
# initramfs without the bootstrap keyfile in it, but left the keyfile and its
# LUKS keyslot alone. If this script is running, the machine has since booted --
# through an initramfs with no key in it, which it could only have done by
# unlocking through the new binding. That boot is the proof the old code assumed
# it had, and this is what collects on it.
#
# Everything here is idempotent and best-effort. A machine that dies partway
# through has a keyfile that opens volumes it is no longer referenced by, which
# costs it nothing, and the next boot finishes the job.
set -u

STAMP=/var/lib/luks-enroll.done
PENDING=/var/lib/luks-enroll.pending
BACKUP=/var/lib/luks-enroll.backup
[ -f "$STAMP" ] && exit 0
[ -f "$PENDING" ] || exit 0

log() { echo "luks-enroll-reap: $*"; }

SLOT="$(sed -n 's/.*rauc\.slot=\([AB]\).*/\1/p' /proc/cmdline 2>/dev/null)"
BOOTED_INITRD="/boot/${SLOT}/initrd.img"

# The one thing worth re-checking: that the initramfs this machine actually
# booted is the keyless one. If a keyfile is still in it, the boot proves
# nothing about the TPM -- the keyfile is what opened the volumes -- and
# removing that keyslot now would be the original bug with extra steps.
if [ -z "$SLOT" ] || [ ! -f "$BOOTED_INITRD" ]; then
    log "cannot identify the initramfs this machine booted; leaving the key in place"
    exit 0
fi
if lsinitramfs "$BOOTED_INITRD" 2>/dev/null | grep -q 'cryptsetup-keys\.d'; then
    log "the booted initramfs still carries the bootstrap keyfile, so this boot"
    log "  does not prove the binding works. Leaving the key in place."
    exit 0
fi

log "this machine booted without a keyfile; the binding works. Removing the bootstrap key."

mapfile -t ENTRIES < <(grep -vE '^\s*(#|$)' /etc/crypttab | awk '{print $1" "$2}')
resolve() { blkid -U "${1#UUID=}" 2>/dev/null; }

removed=0
failed=0
for e in "${ENTRIES[@]}"; do
    name="${e%% *}"; uuid="${e##* }"
    dev="$(resolve "$uuid")"
    kf="/etc/cryptsetup-keys.d/${name}.key"
    [ -b "$dev" ] || { log "cannot resolve $name; leaving its keyslot"; failed=1; continue; }
    [ -f "$kf" ] || continue
    if cryptsetup luksRemoveKey "$dev" "$kf" 2>/dev/null; then
        log "  bootstrap keyslot removed from $name"
        removed=$((removed + 1))
    else
        # Already gone is the common case on a retry, and is success.
        if cryptsetup open --test-passphrase --key-file "$kf" "$dev" 2>/dev/null; then
            log "  WARNING: could not remove the bootstrap keyslot from $name"
            failed=1
        else
            log "  bootstrap keyslot already absent from $name"
        fi
    fi
done

if [ "$failed" = 1 ]; then
    log "not every keyslot could be removed; retrying on the next boot"
    exit 0
fi

rm -f /etc/cryptsetup-keys.d/*.key
sed -i '/KEYFILE_PATTERN/d' /etc/cryptsetup-initramfs/conf-hook 2>/dev/null || true
rm -rf "$BACKUP"

# The initramfs was already rebuilt without the keyfile in phase 1, so this is
# only to drop the KEYFILE_PATTERN line and leave no stale copy behind. Both
# slots, for the same reason phase 1 did it.
update-initramfs -u >/dev/null 2>&1 || log "WARNING: could not rebuild the initramfs"
/usr/local/sbin/ab-sync-boot.sh --slot both >/dev/null 2>&1 || \
    log "WARNING: could not update the slot initramfs copies"

: > "$STAMP"
rm -f "$PENDING"
systemctl disable luks-enroll-reap.service >/dev/null 2>&1 || true
log "done -- $removed keyslot(s) removed; no key material remains on disk"
exit 0
