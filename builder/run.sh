#!/bin/bash
# Convenience wrapper: build the builder image and run it to produce an A/B image
# into ./output. All arguments are passed through to build-image.sh.
#
#   ./run.sh --hostname web01 --username admin --password 's3cret' --image-size 8
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUTPUT_DIR:-$HERE/../output}"

# The builder container runs as the architecture it is building. debootstrap and
# every chroot step then execute natively -- on an arm64 host that makes an arm64
# build as fast as an amd64 one, and on an amd64 host Docker's binfmt handles the
# emulation without build-image.sh needing to know.
ARCH=amd64
prev=""
for a in "$@"; do
    [ "$prev" = "--arch" ] && ARCH="$a"
    prev="$a"
done
case "$ARCH" in
    amd64|arm64) ;;
    *) echo "[run] unsupported --arch '$ARCH' (expected amd64 or arm64)" >&2; exit 2;;
esac
PLATFORM="linux/${ARCH}"
echo "[run] target architecture: $ARCH (builder platform $PLATFORM)"
mkdir -p "$OUT"

echo "[run] building builder image…"
docker build --platform="$PLATFORM" -t "debian-ab-builder:${ARCH}" "$HERE"

echo "[run] building A/B image into $OUT …"
# overlay.d and any --run-script have to be visible inside the container, so
# both are mounted read-only. Read-only because a build must not be able to
# modify the files it is being customized with.
CUSTOM="$(cd "$HERE/.." && pwd)/overlay.d"
mkdir -p "$CUSTOM"
docker run --rm --privileged \
    --platform="$PLATFORM" \
    -v "$OUT":/output \
    -v "$CUSTOM":/overlay.d:ro \
    "debian-ab-builder:${ARCH}" "$@"

echo "[run] artifacts:"
ls -lh "$OUT"
