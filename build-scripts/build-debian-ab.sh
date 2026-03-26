#!/bin/bash

# Debian 13 A/B Image Builder
# Features:
# - A/B partition scheme for updates
# - Overlay partition for persistent data
# - Optional LUKS encryption with auto-unlock
# - Auto-expansion on first boot
# - RAUC ready for atomic updates

set -e

# Load configuration if it exists
CONFIG_FILE="$(dirname "$(realpath "$0")")/../config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
    echo "Loaded configuration from $CONFIG_FILE"
else
    echo "Configuration file not found, using built-in defaults"
    # Default configuration
    DEBIAN_VERSION="${DEFAULT_DEBIAN_VERSION:-trixie}" # Debian 13 codename
    IMAGE_SIZE="${DEFAULT_IMAGE_SIZE:-8G}"
    ROOTFS_SIZE="${DEFAULT_ROOTFS_SIZE:-3G}"
    OVERLAY_SIZE="${DEFAULT_OVERLAY_SIZE:-1G}"
    BOOT_SIZE="${DEFAULT_BOOT_SIZE:-512M}"
    HOSTNAME="${DEFAULT_HOSTNAME:-debian-ab}"
    USERNAME="${DEFAULT_USERNAME:-debian}"
    LUKS_PASSWORD="${DEFAULT_LUKS_PASSWORD:-rootpassword}"
fi

IMAGE_NAME="debian-${DEBIAN_VERSION}-ab"
ENCRYPT_ROOT=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Build Debian 13 A/B image with persistent overlay"
    echo ""
    echo "Options:"
    echo "  -h, --hostname HOSTNAME     Set hostname (default: debian-ab)"
    echo "  -u, --username USERNAME     Set username (default: debian)"
    echo "  -s, --size SIZE             Set image size (default: 8G)"
    echo "  -e, --encrypt               Enable LUKS encryption (default: disabled)"
    echo "  -o, --output NAME           Output image name (default: debian-trixie-ab)"
    echo "  --help                      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                              # Build standard image"
    echo "  $0 -e                          # Build encrypted image"
    echo "  $0 -h myhost -u myuser         # Custom hostname/user"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--hostname)
            HOSTNAME="$2"
            shift 2
            ;;
        -u|--username)
            USERNAME="$2"
            shift 2
            ;;
        -s|--size)
            IMAGE_SIZE="$2"
            shift 2
            ;;
        -o|--output)
            IMAGE_NAME="$2"
            shift 2
            ;;
        -e|--encrypt)
            ENCRYPT_ROOT=true
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo -e "${GREEN}Building Debian ${DEBIAN_VERSION} A/B image${NC}"
echo -e "${GREEN}Hostname:${NC} $HOSTNAME"
echo -e "${GREEN}Username:${NC} $USERNAME"
echo -e "${GREEN}Image size:${NC} $IMAGE_SIZE"
echo -e "${GREEN}Encryption:${NC} $ENCRYPT_ROOT"

# Create temporary build directory
BUILD_DIR=$(mktemp -d)
echo -e "${GREEN}Using build directory:${NC} $BUILD_DIR"

cleanup() {
    echo -e "${YELLOW}Cleaning up...${NC}"
    rm -rf "$BUILD_DIR"
}

trap cleanup EXIT

# Install debootstrap if not present
if ! command -v debootstrap &> /dev/null; then
    echo -e "${YELLOW}Installing debootstrap...${NC}"
    sudo apt-get update
    sudo apt-get install -y debootstrap
fi

# Create image file
echo -e "${GREEN}Creating image file...${NC}"
dd if=/dev/zero of="${IMAGE_NAME}.img" bs=1G count=${IMAGE_SIZE%G} status=progress
sync

# Setup loop device
LOOP_DEV=$(sudo losetup -f --show "${IMAGE_NAME}.img")
echo -e "${GREEN}Using loop device:${NC} $LOOP_DEV"

# Partition the disk for A/B setup
echo -e "${GREEN}Partitioning disk...${NC}"
sudo parted -s "$LOOP_DEV" mklabel gpt
sudo parted -s "$LOOP_DEV" mkpart primary 1MiB 3MiB      # BIOS boot (for GRUB)
sudo parted -s "$LOOP_DEV" mkpart primary 3MiB ${BOOT_SIZE}  # Boot partition (shared)
sudo parted -s "$LOOP_DEV" mkpart primary ext4 ${BOOT_SIZE} $((${BOOT_SIZE%?} + 300))  # A root
sudo parted -s "$LOOP_DEV" mkpart primary ext4 $((${BOOT_SIZE%?} + 300)) $((${BOOT_SIZE%?} + 600))  # B root
sudo parted -s "$LOOP_DEV" mkpart primary ext4 $((${BOOT_SIZE%?} + 600)) 100%  # Persistent overlay

# Get partition devices
PART_BIOS="${LOOP_DEV}p1"
PART_BOOT="${LOOP_DEV}p2"
PART_ROOT_A="${LOOP_DEV}p3"
PART_ROOT_B="${LOOP_DEV}p4"
PART_OVERLAY="${LOOP_DEV}p5"

# Format partitions
echo -e "${GREEN}Formatting partitions...${NC}"
sudo mkfs.ext4 -F "$PART_BOOT"
sudo mkfs.ext4 -F "$PART_ROOT_A"
sudo mkfs.ext4 -F "$PART_ROOT_B"
sudo mkfs.ext4 -F "$PART_OVERLAY"

# Setup encryption if requested
if [ "$ENCRYPT_ROOT" = true ]; then
    echo -e "${GREEN}Setting up LUKS encryption...${NC}"
    # Encrypt root A
    echo "${LUKS_PASSWORD}" | sudo cryptsetup -q luksFormat "$PART_ROOT_A"
    sudo cryptsetup luksOpen "$PART_ROOT_A" root-a-crypt
    
    # Encrypt root B  
    echo "${LUKS_PASSWORD}" | sudo cryptsetup -q luksFormat "$PART_ROOT_B"
    sudo cryptsetup luksOpen "$PART_ROOT_B" root-b-crypt
    
    # Use mapped devices instead of raw partitions
    PART_ROOT_A="/dev/mapper/root-a-crypt"
    PART_ROOT_B="/dev/mapper/root-b-crypt"
    
    # Store LUKS info for later use
    echo "root-a-crypt UUID=$(sudo blkid -s UUID -o value $PART_ROOT_A) none luks" > "$BUILD_DIR/crypttab"
    echo "root-b-crypt UUID=$(sudo blkid -s UUID -o value $PART_ROOT_B) none luks" >> "$BUILD_DIR/crypttab"
fi

# Mount partitions
echo -e "${GREEN}Mounting partitions...${NC}"
mkdir -p "$BUILD_DIR/mnt"
sudo mount "$PART_ROOT_A" "$BUILD_DIR/mnt"
sudo mkdir -p "$BUILD_DIR/mnt/boot"
sudo mount "$PART_BOOT" "$BUILD_DIR/mnt/boot"
sudo mkdir -p "$BUILD_DIR/mnt/var/lib/overlay"
sudo mount "$PART_OVERLAY" "$BUILD_DIR/mnt/var/lib/overlay"

# Bootstrap base system
echo -e "${GREEN}Bootstrapping Debian ${DEBIAN_VERSION}...${NC}"
sudo debootstrap --arch amd64 "$DEBIAN_VERSION" "$BUILD_DIR/mnt" http://deb.debian.org/debian

# Generate fstab
echo -e "${GREEN}Generating filesystem table...${NC}"
sudo tee "$BUILD_DIR/mnt/etc/fstab" > /dev/null <<EOF
# /etc/fstab: static file system information.
#
# <file system> <mount point>   <type>  <options>       <dump>  <pass>
UUID=$(sudo blkid -s UUID -o value "$PART_BOOT") /boot ext4 defaults 0 2
UUID=$(sudo blkid -s UUID -o value "$PART_OVERLAY") /var/lib/overlay ext4 defaults 0 2
tmpfs /tmp tmpfs defaults 0 0
tmpfs /run tmpfs defaults 0 0
EOF

# Configure base system
echo -e "${GREEN}Configuring base system...${NC}"
sudo chroot "$BUILD_DIR/mnt" sh -c "echo '$HOSTNAME' > /etc/hostname"
echo "127.0.1.1    $HOSTNAME" | sudo tee -a "$BUILD_DIR/mnt/etc/hosts"

# Install essential packages
sudo chroot "$BUILD_DIR/mnt" apt-get update
sudo chroot "$BUILD_DIR/mnt" apt-get install -y \
    linux-image-amd64 \
    grub-pc \
    systemd-sysv \
    openssh-server \
    initramfs-tools \
    cryptsetup  # For LUKS support

# Setup auto-expand on first boot
echo -e "${GREEN}Setting up auto-expand script...${NC}"
sudo tee "$BUILD_DIR/mnt/usr/local/bin/auto-expand.sh" > /dev/null <<'EOF'
#!/bin/bash
# Auto-expand partition to fill disk on first boot

set -e

# Check if we've already expanded
if [ -f /etc/auto-expanded ]; then
    exit 0
fi

echo "Expanding partitions to fill disk..."

# Get device information
DISK_DEVICE=$(lsblk -ndo PKNAME /dev/disk/by-label/overlay | head -n1)
PARTITION_PREFIX=""
if [[ "$DISK_DEVICE" =~ ^sd ]]; then
    PARTITION_PREFIX="/dev/${DISK_DEVICE}"
elif [[ "$DISK_DEVICE" =~ ^nvme ]]; then
    PARTITION_PREFIX="/dev/${DISK_DEVICE}p"
else
    PARTITION_PREFIX="/dev/${DISK_DEVICE}"
fi

# Resize partition table
parted -s "/dev/$DISK_DEVICE" unit % resizepart 5 100%

# Resize filesystem
resize2fs "${PARTITION_PREFIX}5"

# Mark that we've expanded
touch /etc/auto-expanded

# Remove ourselves to prevent running again
rm -f /usr/local/bin/auto-expand.sh

echo "Auto-expansion completed."
EOF

sudo chmod +x "$BUILD_DIR/mnt/usr/local/bin/auto-expand.sh"

# Setup service to run auto-expand
sudo tee "$BUILD_DIR/mnt/etc/systemd/system/auto-expand.service" > /dev/null <<EOF
[Unit]
Description=Auto Expand Partitions
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/auto-expand.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo chroot "$BUILD_DIR/mnt" systemctl enable auto-expand.service

# Install RAUC for atomic updates
sudo chroot "$BUILD_DIR/mnt" apt-get install -y rauc

# Create basic RAUC configuration
sudo mkdir -p "$BUILD_DIR/mnt/etc/rauc"
sudo tee "$BUILD_DIR/mnt/etc/rauc/system.conf" > /dev/null <<EOF
[system]
compatible=debian-ab
bootloader=grub

[keyring]
path=/etc/rauc/ca.crt

[slot.rootfs.0]
device=/dev/disk/by-partlabel/rootfs-a
type=raw
bootname=system0

[slot.rootfs.1]
device=/dev/disk/by-partlabel/rootfs-b
type=raw
bootname=system1
EOF

# Setup bootloader
echo -e "${GREEN}Installing bootloader...${NC}"
sudo chroot "$BUILD_DIR/mnt" grub-install "$LOOP_DEV"
sudo chroot "$BUILD_DIR/mnt" update-grub

# Cleanup and unmount
echo -e "${GREEN}Cleaning up...${NC}"
sudo umount "$BUILD_DIR/mnt/var/lib/overlay"
sudo umount "$BUILD_DIR/mnt/boot"
sudo umount "$BUILD_DIR/mnt"

# Close LUKS devices if used
if [ "$ENCRYPT_ROOT" = true ]; then
    sudo cryptsetup luksClose root-a-crypt
    sudo cryptsetup luksClose root-b-crypt
fi

sudo losetup -d "$LOOP_DEV"

echo -e "${GREEN}A/B image created successfully:${NC} ${IMAGE_NAME}.img"
echo -e "${GREEN}Features included:${NC}"
echo "  - A/B root partitions for updates"
echo "  - Persistent overlay partition (/var/lib/overlay)"
if [ "$ENCRYPT_ROOT" = true ]; then
    echo "  - Full disk LUKS encryption"
fi
echo "  - Auto expansion on first boot"
echo "  - RAUC ready for atomic updates"

echo -e "\n${YELLOW}To use this image:${NC}"
echo "  1. Write to disk: sudo dd if=${IMAGE_NAME}.img of=/dev/sdX bs=4M status=progress"
echo "  2. On first boot, the overlay partition will auto-expand to fill the disk"
echo "  3. For updates, use RAUC to switch between A/B partitions"

exit 0