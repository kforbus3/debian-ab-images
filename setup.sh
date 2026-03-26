#!/bin/bash

# Setup script for Debian A/B Image System

set -e

echo "Setting up Debian A/B Image System..."

# Check if we're on a Debian-based system
if ! command -v apt-get &> /dev/null; then
    echo "Warning: This system doesn't appear to be Debian-based."
    echo "The build scripts are designed for Debian/Ubuntu systems."
    echo "You may need to adapt them for your distribution."
fi

# Install required packages
echo "Installing required packages..."
sudo apt-get update

REQUIRED_PACKAGES=(
    debootstrap
    parted
    cryptsetup
    dosfstools
    qemu-utils
    openssl
)

MISSING_PACKAGES=()
for pkg in "${REQUIRED_PACKAGES[@]}"; do
    if ! dpkg -l | grep -q "^ii  $pkg "; then
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo "Installing missing packages: ${MISSING_PACKAGES[*]}"
    sudo apt-get install -y "${MISSING_PACKAGES[@]}"
else
    echo "All required packages are already installed."
fi

# Make scripts executable
echo "Making scripts executable..."
find build-scripts -name "*.sh" -exec chmod +x {} \;

echo "Setup complete!"

echo ""
echo "Next steps:"
echo "1. Review the configuration in config.sh"
echo "2. Generate certificates: make certs"
echo "3. Build an image: make build"
echo "4. For an encrypted image: make build-encrypted"
echo ""
echo "For more options, run: make help"