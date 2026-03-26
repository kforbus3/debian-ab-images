#!/bin/bash

# Script to generate certificates for RAUC
# This creates a CA key/cert and application key/cert for signing bundles

set -e

# Configuration
OUTPUT_DIR="./certs"
CA_KEY="$OUTPUT_DIR/ca.key.pem"
CA_CERT="$OUTPUT_DIR/ca.cert.pem"
APP_KEY="$OUTPUT_DIR/app-key.pem"
APP_CERT="$OUTPUT_DIR/app-cert.pem"
APP_REQ="$OUTPUT_DIR/app-req.pem"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Generate certificates for RAUC"
    echo ""
    echo "Options:"
    echo "  -o, --output DIR           Output directory (default: $OUTPUT_DIR)"
    echo "  --help                     Show this help message"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            OUTPUT_DIR="$2"
            CA_KEY="$OUTPUT_DIR/ca.key.pem"
            CA_CERT="$OUTPUT_DIR/ca.cert.pem"
            APP_KEY="$OUTPUT_DIR/app-key.pem"
            APP_CERT="$OUTPUT_DIR/app-cert.pem"
            APP_REQ="$OUTPUT_DIR/app-req.pem"
            shift 2
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

echo -e "${GREEN}Generating RAUC certificates...${NC}"
echo -e "${GREEN}Output directory:${NC} $OUTPUT_DIR"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Check if certificates already exist
if [[ -f "$CA_CERT" && -f "$APP_CERT" ]]; then
    echo -e "${YELLOW}Certificates already exist. Skipping generation.${NC}"
    echo -e "${YELLOW}Delete them if you want to regenerate.${NC}"
    exit 0
fi

# Generate CA key and certificate
echo -e "${GREEN}Generating CA key and certificate...${NC}"
openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout "$CA_KEY" \
    -out "$CA_CERT" \
    -sha256 -days 3650 \
    -subj "/CN=RAUC Demo CA"

# Generate application key
echo -e "${GREEN}Generating application key...${NC}"
openssl req -newkey rsa:4096 -nodes \
    -keyout "$APP_KEY" \
    -out "$APP_REQ" \
    -subj "/CN=RAUC Demo Application"

# Sign the application certificate with the CA
echo -e "${GREEN}Signing application certificate...${NC}"
openssl x509 -req \
    -in "$APP_REQ" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial \
    -out "$APP_CERT" \
    -sha256 -days 365

# Clean up the request file
rm -f "$APP_REQ"

# Display certificate information
echo -e "${GREEN}Certificate information:${NC}"
echo -e "${GREEN}CA Certificate:${NC}"
openssl x509 -in "$CA_CERT" -noout -text | head -20
echo ""
echo -e "${GREEN}Application Certificate:${NC}"
openssl x509 -in "$APP_CERT" -noout -text | head -20

echo -e "${GREEN}Certificates generated successfully:${NC}"
echo "  CA Key:      $CA_KEY"
echo "  CA Cert:     $CA_CERT"
echo "  App Key:     $APP_KEY"
echo "  App Cert:    $APP_CERT"

echo -e "\n${YELLOW}Security Notes:${NC}"
echo "1. Keep the CA private key secure - it can sign any certificate"
echo "2. Keep the application private key secure - it signs your bundles"
echo "3. The CA certificate can be distributed with your devices"
echo "4. For production, use longer expiration dates and stronger procedures"

exit 0