#!/bin/bash

# Configuration file for Debian A/B image building

# Default settings
DEFAULT_DEBIAN_VERSION="trixie"  # Debian 13
DEFAULT_IMAGE_SIZE="8G"
DEFAULT_ROOTFS_SIZE="3G"
DEFAULT_OVERLAY_SIZE="1G"
DEFAULT_BOOT_SIZE="512M"
DEFAULT_HOSTNAME="debian-ab"
DEFAULT_USERNAME="debian"

# Directory paths
BUILD_SCRIPTS_DIR="$(pwd)/build-scripts"
CERTS_DIR="$(pwd)/certs"
BUNDLES_DIR="$(pwd)/bundles"

# RAUC settings
RAUC_COMPATIBLE="debian-ab"
RAUC_BOOTLOADER="grub"

# Encryption settings
DEFAULT_ENCRYPT_ENABLED=false
# WARNING: In production, never hardcode passwords like this
# This is just for demonstration purposes
DEFAULT_LUKS_PASSWORD="rootpassword"

# Auto-expand settings
AUTO_EXPAND_ENABLED=true

# Build settings
CLEAN_BUILD=false
VERBOSE_OUTPUT=false

# Packages to install by default
DEFAULT_PACKAGES=(
    "linux-image-amd64"
    "grub-pc"
    "systemd-sysv"
    "openssh-server"
    "initramfs-tools"
    "cryptsetup"
    "rauc"
)

# Additional packages that might be useful
OPTIONAL_PACKAGES=(
    "vim"
    "curl"
    "wget"
    "htop"
    "net-tools"
    "iputils-ping"
)