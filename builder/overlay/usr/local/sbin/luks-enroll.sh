#!/bin/bash
# First-boot LUKS enrollment. The image ships with a bootstrap keyfile in the
# initramfs so the very first boot unlocks unattended; this service then binds
# the LUKS volumes to the machine's TPM2 (or a Tang server), rebuilds the
# initramfs, and DESTROYS the bootstrap keyfile so no key remains on disk.
#
# It is best-effort and safe: if enrollment fails, the keyfile is left in place
# so the machine still boots (less secure, but never bricked). It retries on the
# next boot until it succeeds.
set -u

CONF=/etc/luks-enroll.conf
STAMP=/var/lib/luks-enroll.done
[ -f "$STAMP" ] && exit 0
[ -f "$CONF" ] || exit 0
# shellcheck disable=SC1090
. "$CONF"
METHOD="${METHOD:-}"
TANG_URL="${TANG_URL:-}"

log() { echo "luks-enroll: $*"; }

# Collect (name, device) pairs from crypttab.
mapfile -t ENTRIES < <(grep -vE '^\s*(#|$)' /etc/crypttab | awk '{print $1" "$2}')
[ "${#ENTRIES[@]}" -gt 0 ] || { log "no crypttab entries"; exit 0; }

resolve() { blkid -U "${1#UUID=}" 2>/dev/null; }

enrolled_all=1
for e in "${ENTRIES[@]}"; do
    name="${e%% *}"; uuid="${e##* }"
    dev="$(resolve "$uuid")"
    kf="/etc/cryptsetup-keys.d/${name}.key"
    [ -b "$dev" ] || { enrolled_all=0; continue; }
    [ -f "$kf" ] || continue   # already enrolled / no bootstrap key

    case "$METHOD" in
        tpm2)
            log "enrolling TPM2 for $name ($dev)"
            if systemd-cryptenroll --unlock-key-file="$kf" --tpm2-device=auto "$dev"; then
                log "TPM2 enrolled for $name"
            else
                log "TPM2 enrollment FAILED for $name (will retry next boot)"; enrolled_all=0
            fi
            ;;
        tang)
            log "binding Tang for $name ($dev) -> $TANG_URL"
            if clevis luks bind -y -k "$kf" -d "$dev" tang "{\"url\":\"$TANG_URL\"}"; then
                log "Tang bound for $name"
            else
                log "Tang bind FAILED for $name (will retry next boot)"; enrolled_all=0
            fi
            ;;
        *) log "unknown METHOD=$METHOD"; exit 0;;
    esac
done

[ "$enrolled_all" = 1 ] || { log "not all volumes enrolled; keeping keyfile for now"; exit 0; }

log "all volumes enrolled — switching off the bootstrap keyfile"
# Point crypttab at TPM/Tang (no keyfile) and rebuild the initramfs.
if [ "$METHOD" = tpm2 ]; then
    sed -i -E 's#/etc/cryptsetup-keys.d/[^ ]+#none#; s#luks,discard([^ ]*)#luks,discard\1,tpm2-device=auto#' /etc/crypttab
else
    sed -i -E 's#/etc/cryptsetup-keys.d/[^ ]+#none#' /etc/crypttab
fi

# Remove the bootstrap keyfile keyslots and files.
for e in "${ENTRIES[@]}"; do
    name="${e%% *}"; uuid="${e##* }"
    dev="$(resolve "$uuid")"; kf="/etc/cryptsetup-keys.d/${name}.key"
    [ -b "$dev" ] && [ -f "$kf" ] && cryptsetup luksRemoveKey "$dev" "$kf" 2>/dev/null
done
rm -f /etc/cryptsetup-keys.d/*.key
sed -i '/KEYFILE_PATTERN/d' /etc/cryptsetup-initramfs/conf-hook 2>/dev/null || true

update-initramfs -u
touch "$STAMP"
systemctl disable luks-enroll.service >/dev/null 2>&1 || true
log "done — key material removed from disk"
