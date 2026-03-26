#!/bin/bash

# Script to create RAUC update bundles for A/B images
# This would typically be run on a build server after creating a new rootfs

set -e

# Configuration
BUNDLE_NAME="debian-ab-update"
VERSION="1.0"
COMMIT_ID=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
OUTPUT_DIR="./bundles"
CERT_FILE="./certs/app-cert.pem"
KEY_FILE="./certs/app-key.pem"
CA_FILE="./certs/ca.cert.pem"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [OPTIONS] ROOTFS_DIR"
    echo "Create RAUC update bundle for A/B images"
    echo ""
    echo "Arguments:"
    echo "  ROOTFS_DIR     Directory containing root filesystem to bundle"
    echo ""
    echo "Options:"
    echo "  -v, --version VERSION      Bundle version (default: $VERSION)"
    echo "  -n, --name NAME            Bundle name (default: $BUNDLE_NAME)"
    echo "  -o, --output DIR           Output directory (default: $OUTPUT_DIR)"
    echo "  -c, --cert FILE            Certificate file (default: $CERT_FILE)"
    echo "  -k, --key FILE             Private key file (default: $KEY_FILE)"
    echo "  -a, --ca FILE              CA certificate file (default: $CA_FILE)"
    echo "  --help                     Show this help message"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -n|--name)
            BUNDLE_NAME="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -c|--cert)
            CERT_FILE="$2"
            shift 2
            ;;
        -k|--key)
            KEY_FILE="$2"
            shift 2
            ;;
        -a|--ca)
            CA_FILE="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
        *)
            ROOTFS_DIR="$1"
            shift
            ;;
    esac
done

# Validate inputs
if [[ -z "$ROOTFS_DIR" ]]; then
    echo -e "${RED}Error: ROOTFS_DIR not specified${NC}"
    usage
    exit 1
fi

if [[ ! -d "$ROOTFS_DIR" ]]; then
    echo -e "${RED}Error: Root filesystem directory not found: $ROOTFS_DIR${NC}"
    exit 1
fi

if [[ ! -f "$CERT_FILE" ]]; then
    echo -e "${YELLOW}Warning: Certificate file not found: $CERT_FILE${NC}"
    echo -e "${YELLOW}You'll need to generate certificates for production use${NC}"
fi

if [[ ! -f "$KEY_FILE" ]]; then
    echo -e "${YELLOW}Warning: Private key file not found: $KEY_FILE${NC}"
    echo -e "${YELLOW}You'll need to generate keys for production use${NC}"
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

echo -e "${GREEN}Creating RAUC bundle...${NC}"
echo -e "${GREEN}Bundle name:${NC} $BUNDLE_NAME"
echo -e "${GREEN}Version:${NC} $VERSION"
echo -e "${GREEN}Commit:${NC} $COMMIT_ID"
echo -e "${GREEN}Root FS:${NC} $ROOTFS_DIR"

# Create manifest file
MANIFEST_FILE=$(mktemp)
cat > "$MANIFEST_FILE" <<EOF
[update]
compatible=debian-ab
version=$VERSION+$COMMIT_ID

[handler]
name=custom-script
args=update-script.sh

[slot.rootfs.img]
filename=rootfs.img
install=True
EOF

echo -e "${GREEN}Manifest created:${NC} $MANIFEST_FILE"

# Create temporary directory for bundle contents
TMP_DIR=$(mktemp -d)
echo -e "${GREEN}Using temp directory:${NC} $TMP_DIR"

# Copy rootfs to temporary location
echo -e "${GREEN}Preparing rootfs image...${NC}"
# This is a simplified approach - in practice, you'd want to create
# a proper filesystem image rather than just tarring the directory
tar -czf "$TMP_DIR/rootfs.tar.gz" -C "$ROOTFS_DIR" .

# Create update script
cat > "$TMP_DIR/update-script.sh" <<'EOF'
#!/bin/sh
set -e

# This script runs during RAUC installation

# Mount the target slot
mount_point=$(mktemp -d)
mount -t ext4 "$RAUC_SLOT_DEVICE" "$mount_point"

# Extract the new rootfs
tar -xzf rootfs.tar.gz -C "$mount_point"

# Unmount the slot
umount "$mount_point"
rmdir "$mount_point"

# Report success
exit 0
EOF

chmod +x "$TMP_DIR/update-script.sh"

# Create the bundle if we have signing keys
if [[ -f "$CERT_FILE" && -f "$KEY_FILE" && -f "$CA_FILE" ]]; then
    echo -e "${GREEN}Creating signed bundle...${NC}"
    
    # In a real implementation, we would use:
    # rauc bundle --cert="$CERT_FILE" --key="$KEY_FILE" --keyring="$CA_FILE" \
    #     "$TMP_DIR" "$OUTPUT_DIR/${BUNDLE_NAME}-${VERSION}.raucb"
    
    echo -e "${YELLOW}Skipping actual bundle creation (needs RAUC tools)${NC}"
    echo -e "${YELLOW}In practice, would create:$OUTPUT_DIR/${BUNDLE_NAME}-${VERSION}.raucb${NC}"
else
    # Create unsigned development bundle
    echo -e "${GREEN}Creating development bundle (unsigned)...${NC}"
    
    # Package everything into a tarball for demonstration
    tar -czf "$OUTPUT_DIR/${BUNDLE_NAME}-${VERSION}-dev.tar.gz" -C "$TMP_DIR" .
    echo -e "${GREEN}Development bundle created:${NC} $OUTPUT_DIR/${BUNDLE_NAME}-${VERSION}-dev.tar.gz"
fi

# Cleanup
rm -rf "$TMP_DIR"
rm -f "$MANIFEST_FILE"

echo -e "${GREEN}RAUC bundle preparation completed.${NC}"

# Instructions for certificate generation
if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
    echo -e "\n${YELLOW}To create certificates for production use:${NC}"
    echo "  # Create CA key and certificate"
    echo "  openssl req -x509 -newkey rsa:4096 -nodes -keyout ca.key.pem -days 3650 -out ca.cert.pem"
    echo ""
    echo "  # Create application key and certificate signing request"
    echo "  openssl req -newkey rsa:4096 -nodes -keyout app-key.pem -out app-req.pem"
    echo ""
    echo "  # Sign the application certificate with the CA"
    echo "  openssl x509 -req -in app-req.pem -CA ca.cert.pem -CAkey ca.key.pem -CAcreateserial -out app-cert.pem -days 365"
fi

exit 0