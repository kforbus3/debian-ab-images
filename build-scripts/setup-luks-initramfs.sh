#!/bin/bash

# Script to setup LUKS auto-unlock in initramfs
# This should be run inside the chroot environment during image creation

set -e

echo "Setting up LUKS auto-unlock in initramfs..."

# Create key directory
mkdir -p /etc/luks-keys

# Generate a random key
openssl rand -hex 32 > /etc/luks-keys/root.key

# Add the key to LUKS volumes (will be done during actual deployment)
# For now, we'll just prepare the framework
echo "#!/bin/sh
# LUKS Auto-unlock script for initramfs

PREREQ=\"\"

prereqs() {
    echo \"\$PREREQ\"
}

case \$1 in
    prereqs)
        prereqs
        exit 0
        ;;
esac

. /scripts/functions

# Load crypto modules
load_modules() {
    modprobe -q aesni_intel 2>/dev/null || true
    modprobe -q dm_crypt 2>/dev/null || true
}

# Unlock encrypted volumes
unlock_volumes() {
    if [ -f /etc/luks-keys/root.key ]; then
        # Try to unlock root-a-crypt
        if cryptsetup luksOpen /dev/disk/by-partuuid/\$(sed -n 's/root-a-crypt UUID=\(.*\) .*/\1/p' /etc/crypttab) root-a-crypt --key-file /etc/luks-keys/root.key 2>/dev/null; then
            verbose && log_success_msg \"Unlocked root-a-crypt\"
        else
            verbose && log_failure_msg \"Failed to unlock root-a-crypt\"
        fi
        
        # Try to unlock root-b-crypt
        if cryptsetup luksOpen /dev/disk/by-partuuid/\$(sed -n 's/root-b-crypt UUID=\(.*\) .*/\1/p' /etc/crypttab) root-b-crypt --key-file /etc/luks-keys/root.key 2>/dev/null; then
            verbose && log_success_msg \"Unlocked root-b-crypt\"
        else
            verbose && log_failure_msg \"Failed to unlock root-b-crypt\"
        fi
    fi
}

load_modules
unlock_volumes
" > /etc/initramfs-tools/scripts/local-top/luks-auto-unlock

# Make the script executable
chmod +x /etc/initramfs-tools/scripts/local-top/luks-auto-unlock

# Add crypto modules to initramfs
echo "dm-crypt" >> /etc/initramfs-tools/modules
echo "aesni_intel" >> /etc/initramfs-tools/modules

# Include key files in initramfs
echo "KEYFILE_PATTERN=/etc/luks-keys/*.key" >> /etc/cryptsetup-initramfs/conf-hook

# Rebuild initramfs
update-initramfs -u

echo "LUKS auto-unlock setup completed."
echo "Remember to securely store and manage the key file for production use."