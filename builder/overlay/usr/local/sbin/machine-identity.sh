#!/bin/bash
# Give each imaged machine its own identity, shared across the A/B slots.
#
# The image ships with a blank /etc/machine-id and no SSH host keys (both are
# stripped at build time so every machine isn't a bit-for-bit identity clone).
# On each boot this restores the machine's identity from the persistent overlay
# partition, generating and storing it first if this is a brand-new machine.
# Because the overlay is shared by both root slots, the identity survives A/B
# updates and slot switches. Best-effort: never fails the boot.
set -u

STATE=/var/lib/overlay/persistent/identity
mkdir -p "$STATE" 2>/dev/null || exit 0
chmod 700 "$STATE"

# --- SSH host keys ---
if compgen -G "$STATE/ssh_host_*_key" >/dev/null; then
    # A/B twin slot or later boot: adopt the stored keys (idempotent).
    cp -a "$STATE"/ssh_host_* /etc/ssh/ 2>/dev/null
else
    ssh-keygen -A >/dev/null 2>&1
    cp -a /etc/ssh/ssh_host_* "$STATE"/ 2>/dev/null
fi

# --- machine-id ---
# systemd generates a fresh id early in boot when /etc/machine-id is blank.
if [ -s "$STATE/machine-id" ]; then
    if ! cmp -s "$STATE/machine-id" /etc/machine-id; then
        # First boot of the other slot: adopt the machine's stored id.
        # Fully effective from the next boot of this slot; harmless meanwhile.
        cp "$STATE/machine-id" /etc/machine-id 2>/dev/null
    fi
elif [ -s /etc/machine-id ]; then
    cp /etc/machine-id "$STATE/machine-id" 2>/dev/null
fi

exit 0
