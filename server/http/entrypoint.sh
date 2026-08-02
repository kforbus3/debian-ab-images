#!/bin/sh
# Render boot.ipxe from environment and expose the imager + images over HTTP.
set -eu

: "${SERVER_IP:?Set SERVER_IP to the provisioning server's IP address}"
: "${IMAGE_FILE:?Set IMAGE_FILE to the image filename in ./output (e.g. debian-trixie-ab.img.zst)}"
ACTION="${ACTION:-reboot}"

mkdir -p /srv/http /data/hosts
# /data is the mounted ./output directory (images at the root, imager/ inside).
ln -sfn /data        /srv/http/images
ln -sfn /data/imager /srv/http/imager
# Per-machine boot scripts written by the web UI. Created above if absent so a
# missing directory 404s per request rather than breaking nginx at startup.
ln -sfn /data/hosts  /srv/http/hosts

export SERVER_IP IMAGE_FILE ACTION
# boot.ipxe dispatches on MAC and falls back to default.ipxe.
envsubst '${SERVER_IP}'                                < /boot.ipxe.tmpl    > /srv/http/boot.ipxe
envsubst '${SERVER_IP} ${IMAGE_FILE} ${ACTION}'        < /default.ipxe.tmpl > /srv/http/default.ipxe
# Bind the listener to the provisioning IP rather than every host interface.
envsubst '${SERVER_IP}' < /nginx.conf.tmpl > /etc/nginx/conf.d/default.conf

echo "----- rendered /srv/http/boot.ipxe -----"
cat /srv/http/boot.ipxe
echo "----------------------------------------"

exec nginx -g 'daemon off;'
