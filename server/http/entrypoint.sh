#!/bin/sh
# Render boot.ipxe from environment and expose the imager + images over HTTP.
set -eu

: "${SERVER_IP:?Set SERVER_IP to the provisioning server's IP address}"
: "${IMAGE_FILE:?Set IMAGE_FILE to the image filename in ./output (e.g. debian-trixie-ab.img.zst)}"
ACTION="${ACTION:-reboot}"

mkdir -p /srv/http
# /data is the mounted ./output directory (images at the root, imager/ inside).
ln -sfn /data        /srv/http/images
ln -sfn /data/imager /srv/http/imager

export SERVER_IP IMAGE_FILE ACTION
envsubst '${SERVER_IP} ${IMAGE_FILE} ${ACTION}' < /boot.ipxe.tmpl > /srv/http/boot.ipxe

echo "----- rendered /srv/http/boot.ipxe -----"
cat /srv/http/boot.ipxe
echo "----------------------------------------"

exec nginx -g 'daemon off;'
