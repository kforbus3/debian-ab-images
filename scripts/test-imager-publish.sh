#!/bin/bash
# The imager artifact a netbooting machine fetches must never be a partial one.
#
# What happened: build-imager.sh packed the initramfs with a redirect straight
# onto output/imager/initramfs.img -- the file the PXE server is serving. The
# redirect truncates it at the start of the pack and fills it over the several
# minutes the module tree takes. A machine that netbooted during a rebuild got a
# complete kernel and a half-written archive, found no /init in it, and panicked
# with "No working init found". Nothing in the build output said anything, and by
# the time anyone looked the file was whole again.
#
# This covers the two properties that stop it: the archive is checked before it is
# published, and publishing is atomic so a machine sees only whole artifacts.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VERIFY="$HERE/../imager/verify-initramfs.sh"
FAILED=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ok()   { echo -e "  \033[0;32mPASS\033[0m $*"; }
bad()  { echo -e "  \033[0;31mFAIL\033[0m $*"; FAILED=1; }

# A stand-in for the real initramfs: the same two entries the verifier looks for,
# padded so truncating it lands in the middle rather than past the end.
make_initramfs() {
    local dir="$WORK/root" out="$1"
    rm -rf "$dir"; mkdir -p "$dir/bin"
    printf '#!/bin/busybox sh\necho imager\n' > "$dir/init"
    chmod +x "$dir/init"
    head -c 2000000 /dev/urandom > "$dir/bin/busybox"
    chmod +x "$dir/bin/busybox"
    head -c 4000000 /dev/urandom > "$dir/filler.bin"
    ( cd "$dir" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$out"
}

echo "== a complete archive verifies =="
make_initramfs "$WORK/good.img"
if "$VERIFY" "$WORK/good.img" >/dev/null 2>&1; then
    ok "a whole initramfs is accepted"
else
    bad "a whole initramfs was rejected: $("$VERIFY" "$WORK/good.img" 2>&1)"
fi

echo "== the failure that shipped: a half-written archive =="
# Exactly what a machine fetched at 10:44 while the pack finished at 10:47.
for frac in 10 50 90; do
    total=$(wc -c < "$WORK/good.img")
    head -c $(( total * frac / 100 )) "$WORK/good.img" > "$WORK/partial.img"
    if "$VERIFY" "$WORK/partial.img" >/dev/null 2>&1; then
        bad "a ${frac}%-written initramfs was accepted — this is the bug"
    else
        ok "a ${frac}%-written initramfs is refused"
    fi
done

echo "== an archive with no /init =="
rm -rf "$WORK/root"; mkdir -p "$WORK/root/bin"
head -c 1000 /dev/urandom > "$WORK/root/bin/busybox"
( cd "$WORK/root" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$WORK/noinit.img"
if "$VERIFY" "$WORK/noinit.img" >/dev/null 2>&1; then
    bad "an initramfs with no /init was accepted"
else
    ok "an initramfs with no /init is refused"
fi

echo "== an archive whose interpreter is missing =="
rm -rf "$WORK/root"; mkdir -p "$WORK/root"
printf '#!/bin/busybox sh\n' > "$WORK/root/init"; chmod +x "$WORK/root/init"
( cd "$WORK/root" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$WORK/nobb.img"
if "$VERIFY" "$WORK/nobb.img" >/dev/null 2>&1; then
    bad "an initramfs with no /bin/busybox was accepted"
else
    ok "an initramfs with no /bin/busybox is refused"
fi

echo "== the verifier does not fail on its own success =="
# grep -q exits at its first match and SIGPIPEs the decompressor; piped straight
# into the check under `set -o pipefail` that reports failure exactly when the
# archive is fine. Run it repeatedly: a SIGPIPE race would show up as a flake.
flaky=0
for _ in $(seq 1 10); do
    "$VERIFY" "$WORK/good.img" >/dev/null 2>&1 || flaky=1
done
[ "$flaky" -eq 0 ] && ok "10/10 runs on a good archive pass" \
                   || bad "the verifier fails intermittently on a good archive (SIGPIPE?)"

echo "== the build publishes atomically =="
# The property, read off the build script: nothing may redirect or copy onto a
# served artifact in place. Both are fetched by netbooting machines.
BUILD="$HERE/../imager/build-imager.sh"
if grep -qE '> *"\$OUT/initramfs\.img"' "$BUILD"; then
    bad "build-imager.sh still writes the initramfs directly onto the served path"
else
    ok "the initramfs is not written directly onto the served path"
fi
if grep -qE 'cp .*"\$OUT/vmlinuz"' "$BUILD"; then
    bad "build-imager.sh still copies the kernel directly onto the served path"
else
    ok "the kernel is not written directly onto the served path"
fi
if grep -q 'mv -f "\$TMP_IMG" "\$OUT/initramfs.img"' "$BUILD"; then
    ok "the initramfs is published with a rename"
else
    bad "the initramfs is not published with a rename"
fi
# A pack that dies must leave the working imager alone, not a stub of a new one.
if grep -q "trap 'rm -f \"\$TMP_IMG\"' EXIT" "$BUILD"; then
    ok "a failed pack cleans up its temporary file"
else
    bad "a failed pack leaves its temporary file behind"
fi

echo
[ "$FAILED" -eq 0 ] && echo -e "\033[0;32mAll imager-publish checks passed\033[0m" \
                    || echo -e "\033[0;31mSome imager-publish checks FAILED\033[0m"
exit "$FAILED"
