#!/bin/sh
# Render boot.ipxe from environment and expose the imager + images over HTTP.
set -eu

: "${SERVER_IP:?Set SERVER_IP to the IP address of the provisioning server}"
: "${IMAGE_FILE:?Set IMAGE_FILE to the image filename in ./output (e.g. debian-trixie-ab.img.zst)}"
ACTION="${ACTION:-reboot}"

mkdir -p /srv/http /data/hosts
# /data is the mounted ./output directory (images at the root, imager/ inside).
ln -sfn /data        /srv/http/images
ln -sfn /data/imager /srv/http/imager
# Per-machine boot scripts written by the web UI. Created above if absent so a
# missing directory 404s per request rather than breaking nginx at startup.
ln -sfn /data/hosts  /srv/http/hosts
# RAUC update bundles, so a machine can be updated in place instead of
# re-imaged: rauc install http://<server>/bundles/<name>.raucb
mkdir -p /data/bundles 2>/dev/null || true
ln -sfn /data/bundles /srv/http/bundles

# UNASSIGNED decides what a machine with no per-machine assignment gets:
#   image (default) — the default image, i.e. plug in a switch and image it all
#   hold            — discovery: print the MAC, touch nothing, retry
RETRY_SECONDS="${RETRY_SECONDS:-30}"
case "${UNASSIGNED:-image}" in
    hold) FALLBACK="unassigned.ipxe";;
    *)    FALLBACK="default.ipxe";;
esac

export SERVER_IP IMAGE_FILE ACTION FALLBACK RETRY_SECONDS
# boot.ipxe dispatches on MAC and falls back to whichever of the two applies.
envsubst '${SERVER_IP} ${FALLBACK}'                    < /boot.ipxe.tmpl       > /srv/http/boot.ipxe
envsubst '${SERVER_IP} ${IMAGE_FILE} ${ACTION}'        < /default.ipxe.tmpl    > /srv/http/default.ipxe
envsubst '${RETRY_SECONDS}'                            < /unassigned.ipxe.tmpl > /srv/http/unassigned.ipxe
# Bind the listener to the provisioning IP rather than every host interface.
envsubst '${SERVER_IP}' < /nginx.conf.tmpl > /etc/nginx/conf.d/default.conf

echo "----- rendered /srv/http/boot.ipxe -----"
cat /srv/http/boot.ipxe
echo "----------------------------------------"

exec nginx -g 'daemon off;'
