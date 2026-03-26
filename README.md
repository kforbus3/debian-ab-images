# Debian 13 A/B Image System

This project provides scripts to build Debian 13 (Trixie) A/B images designed for embedded systems or IoT devices that require atomic updates and persistent storage.

## Features

- **A/B Partition Scheme**: Two root partitions for seamless updates
- **Persistent Overlay**: Separate partition for persistent data storage
- **LUKS Encryption**: Optional full disk encryption with auto-unlock capability
- **Auto Expansion**: Automatically expands to fill the target disk on first boot
- **RAUC Ready**: Pre-configured for atomic updates with RAUC
- **Initramfs Support**: Custom initramfs modules for encryption auto-unlock

## Requirements

- Debian-based system (Debian, Ubuntu, etc.)
- debootstrap
- parted
- cryptsetup (for encryption)
- qemu-utils (for image creation)
- sudo privileges

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd debian-ab-image

# Make the build script executable
chmod +x build-scripts/build-debian-ab.sh
```

## Usage

### Basic Image Creation

```bash
# Create a standard A/B image
./build-scripts/build-debian-ab.sh

# Create with custom hostname and username
./build-scripts/build-debian-ab.sh -h mydevice -u myuser

# Create with encryption
./build-scripts/build-debian-ab.sh -e

# Create with custom size
./build-scripts/build-debian-ab.sh -s 16G
```

### Deploying the Image

```bash
# Write to SD card or USB drive (replace sdX with actual device)
sudo dd if=debian-trixie-ab.img of=/dev/sdX bs=4M status=progress oflag=sync

# Eject the device
sudo eject /dev/sdX
```

## Architecture

### Partition Layout

1. **BIOS Boot Partition** (2MB): For GRUB bootloader
2. **Boot Partition** (512MB): Shared between A/B systems
3. **Root A Partition** (3GB): Primary root filesystem
4. **Root B Partition** (3GB): Backup/Spare root filesystem for updates
5. **Overlay Partition** (Remaining space): Persistent storage mounted at `/var/lib/overlay`

### Overlay Filesystem Structure

Persistent data is stored in the overlay partition, organized as follows:
```
/var/lib/overlay/
├── upper/          # Upper layer for overlay mounts
├── work/           # Work directory for overlay mounts
└── persistent/     # Direct persistent data storage
```

### Encryption Implementation

When encryption is enabled:
- Both Root A and Root B partitions are LUKS encrypted
- Encryption keys are managed through initramfs
- Auto-unlock capability for headless systems

### Auto Expansion

On first boot after deployment:
- The script automatically detects disk size
- Expands the overlay partition to fill remaining space
- Resizes the filesystem accordingly
- Marks completion to prevent re-running

### RAUC Integration

RAUC configuration is pre-installed for atomic updates:
- Configured for A/B update strategy
- Certificate-based bundle verification
- Compatible with standard RAUC update workflows

## Customization

You can modify the build script to:
- Change default partition sizes
- Add additional packages
- Modify default user accounts
- Customize system services
- Adjust RAUC configuration

## Update Process

With RAUC, updates follow this workflow:
1. Create RAUC update bundle
2. Transfer bundle to device
3. Install bundle with RAUC: `rauc install update-bundle.raucb`
4. Reboot to activate new slot

## Security Considerations

- Default passwords should be changed after first boot
- SSH keys should be updated for production use
- Encryption keys must be properly secured
- Firewall rules should be configured appropriately

## Troubleshooting

Common issues and solutions:

### Device Not Booting
- Check partition tables with `fdisk -l`
- Verify bootloader installation
- Ensure partition UUIDs match fstab entries

### Overlay Not Mounting
- Check overlay partition UUID in fstab
- Verify partition exists with `blkid`
- Ensure sufficient space on overlay partition

### Encryption Issues
- Confirm key files are properly stored in initramfs
- Check LUKS header integrity with `cryptsetup luksDump`
- Verify kernel modules for encryption are available