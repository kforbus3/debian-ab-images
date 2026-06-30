#!/bin/bash
# End-to-end imager test (runs inside a privileged qemu container):
#   1. serve /output over HTTP
#   2. boot the imager in QEMU; it fetches the image and writes it to a blank disk
#   3. boot the freshly imaged disk and confirm it reaches a login prompt
set -u
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq qemu-system-x86 python3 >/dev/null 2>&1

cd /output
echo "=== serving /output on :8000 ==="
python3 -m http.server 8000 --bind 127.0.0.1 >/tmp/http.log 2>&1 &
sleep 2

echo "=== creating blank 8G target disk ==="
truncate -s 8G /output/target.img

echo "=== STAGE 1: netboot imager images the blank disk ==="
timeout 900 qemu-system-x86_64 -m 1536 -smp 2 \
  -kernel /output/imager/vmlinuz \
  -initrd /output/imager/initramfs.img \
  -append "imager.url=http://10.0.2.2:8000/debian-trixie-ab.img imager.compress=none imager.action=poweroff console=ttyS0,115200" \
  -drive file=/output/target.img,format=raw,if=virtio \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -nographic -serial mon:stdio -no-reboot 2>&1 | tee /output/imager-e2e.log | grep -aiE "imager|network|target|writing|success|reboot|error|fatal"

echo ""
echo "=== imaged disk partition table ==="
apt-get install -y -qq gdisk >/dev/null 2>&1
sgdisk -p /output/target.img 2>&1 | tail -8

echo ""
echo "=== STAGE 2: boot the freshly imaged disk ==="
timeout 360 qemu-system-x86_64 -m 1024 -smp 2 \
  -drive file=/output/target.img,format=raw,if=virtio \
  -nographic -serial mon:stdio -no-reboot 2>&1 | tee /output/imaged-boot.log | grep -aiE "login:|Debian GNU|Reached target multi-user|Kernel panic|Cannot open root" | tail -5
echo "=== done ==="
