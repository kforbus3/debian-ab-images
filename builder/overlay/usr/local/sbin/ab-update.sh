#!/bin/bash
# Update this machine in place, using the slot it is not running on.
#
#   ab-update                              # newest bundle from the server it was imaged from
#   ab-update http://host/bundles/x.raucb  # a specific bundle
#   ab-update --status                     # which slot is running, and what is on the other
#
# This is what the two root slots are for. The update is written to the inactive
# slot while this one keeps running, the boot order flips, and the machine
# reboots into it. If it fails to come up, GRUB's try-counter falls back to the
# slot you are on now, which is untouched.
#
# The overlay is not part of the update: /home, /etc and everything else written
# since imaging live there and are deliberately carried across. Run
# ab-overlay-diff afterwards if a file from the new image seems not to have
# arrived -- an old copy in the overlay shadows it (see docs/RECOVERY.md).
set -u

MARKER=/boot/ab-deploy.json

usage() { sed -n '2,18p' "$0"; exit 0; }

case "${1:-}" in
    -h|--help) usage;;
    --status)
        rauc status 2>/dev/null || echo "rauc is not available on this system"
        exit $?;;
esac

BUNDLE="${1:-}"

if [ -z "$BUNDLE" ]; then
    # No URL given: ask the server this machine was imaged from. The address is
    # the one the imager left behind, which is the only thing here that knows it.
    [ -r "$MARKER" ] || {
        echo "No bundle URL given, and $MARKER does not exist so there is no server" >&2
        echo "to ask. Pass a URL: ab-update http://<server>/bundles/<name>.raucb" >&2
        exit 2
    }
    base="$(sed -n 's|.*"checkin_url"[[:space:]]*:[[:space:]]*"\(https\?://[^/]*\)/.*|\1|p' "$MARKER")"
    [ -n "$base" ] || { echo "could not read the server address from $MARKER" >&2; exit 2; }

    echo "Looking for the newest bundle on ${base}"
    # nginx autoindex is not enabled, so the listing is not something to parse.
    # The server publishes a small pointer file instead; without it, be explicit
    # rather than guessing at a filename.
    BUNDLE="$(curl -fsS --max-time 15 "${base}/bundles/latest" 2>/dev/null | tr -d '\r\n')"
    [ -n "$BUNDLE" ] || {
        echo "No 'latest' pointer published at ${base}/bundles/latest." >&2
        echo "Create a bundle in the web UI, or pass the URL directly." >&2
        exit 3
    }
    case "$BUNDLE" in
        http://*|https://*) ;;
        *) BUNDLE="${base}/bundles/${BUNDLE}";;
    esac
fi

echo "Installing $BUNDLE"
echo "This writes the inactive slot; the running system is not touched."

LOG="$(mktemp /tmp/ab-update.XXXXXX)"
trap 'rm -f "$LOG"' EXIT

install_bundle() {
    rauc install "$1" 2>&1 | tee "$LOG"
    return "${PIPESTATUS[0]}"
}

rc=0
install_bundle "$BUNDLE" || rc=$?

# RAUC installs from a URL by streaming, and streaming refuses a bundle in the
# older 'plain' format. Downloading it first turns that into an ordinary local
# install, which works for any format -- worth one retry before reporting
# failure, since the alternative is a bundle nobody can install.
if [ "$rc" -ne 0 ] && grep -q "not supported in streaming mode" "$LOG" 2>/dev/null; then
    case "$BUNDLE" in
        http://*|https://*)
            tmp="/var/tmp/$(basename "$BUNDLE")"
            echo ""
            echo "This bundle cannot be streamed; downloading it first."
            if curl -fL --progress-bar -o "$tmp" "$BUNDLE"; then
                rc=0
                install_bundle "$tmp" || rc=$?
                rm -f "$tmp"
            fi
            ;;
    esac
fi

if [ "$rc" -ne 0 ]; then
    {
        echo ""
        echo "Update failed (rauc exit $rc). The running slot is unchanged, so this"
        echo "machine is still bootable. Common causes:"
        echo "  * the bundle is signed by a key this image does not trust"
        echo "    (the certificate has to be inside the image when it is built)"
        echo "  * compatible does not match: $(sed -n 's/^compatible=//p' /etc/rauc/system.conf 2>/dev/null)"
    } >&2
    exit "$rc"
fi

echo ""
echo "Installed. Reboot to switch slots:  systemctl reboot"
echo "If the new slot fails to boot, GRUB falls back to this one automatically."
