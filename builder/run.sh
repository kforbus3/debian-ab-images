#!/bin/bash
# Convenience wrapper: build the builder image and run it to produce an A/B image
# into ./output. All arguments are passed through to build-image.sh.
#
#   ./run.sh --hostname web01 --username admin --password 's3cret' --image-size 8
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUTPUT_DIR:-$HERE/../output}"
mkdir -p "$OUT"

echo "[run] building builder image…"
docker build --platform=linux/amd64 -t debian-ab-builder "$HERE"

echo "[run] building A/B image into $OUT …"
docker run --rm --privileged \
    --platform=linux/amd64 \
    -v "$OUT":/output \
    debian-ab-builder "$@"

echo "[run] artifacts:"
ls -lh "$OUT"
