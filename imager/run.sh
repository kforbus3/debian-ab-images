#!/bin/bash
# Build the netboot imager (kernel + initramfs) into ./output/imager.
#
#   ./run.sh                # amd64 → output/imager/
#   ./run.sh --arch arm64   # arm64 → output/imager/arm64/
#
# The imager is executed by the machine being provisioned, so it has to be built
# for that machine's architecture: an amd64 imager cannot boot an arm64 box.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUTPUT_DIR:-$HERE/../output}"
mkdir -p "$OUT"

ARCH=amd64
prev=""
for a in "$@"; do
    [ "$prev" = "--arch" ] && ARCH="$a"
    prev="$a"
done
case "$ARCH" in
    amd64) KERNEL_PKG=linux-image-amd64;;
    arm64) KERNEL_PKG=linux-image-arm64;;
    *) echo "[run] unsupported --arch '$ARCH' (expected amd64 or arm64)" >&2; exit 2;;
esac
PLATFORM="linux/${ARCH}"

echo "[run] building the $ARCH imager (platform $PLATFORM)"
docker build --platform="$PLATFORM" --build-arg "KERNEL_PKG=$KERNEL_PKG" \
    -t "debian-ab-imager:${ARCH}" "$HERE"
docker run --rm --platform="$PLATFORM" -e "ARCH=$ARCH" -v "$OUT":/output \
    "debian-ab-imager:${ARCH}"

if [ "$ARCH" = amd64 ]; then
    echo "[run] imager artifacts in $OUT/imager"
else
    echo "[run] imager artifacts in $OUT/imager/$ARCH"
fi
