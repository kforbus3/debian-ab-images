#!/bin/bash
# Build the netboot imager (kernel + initramfs) into ./output/imager.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUTPUT_DIR:-$HERE/../output}"
mkdir -p "$OUT"

docker build --platform=linux/amd64 -t debian-ab-imager "$HERE"
docker run --rm --platform=linux/amd64 -v "$OUT":/output debian-ab-imager
echo "[run] imager artifacts in $OUT/imager"
