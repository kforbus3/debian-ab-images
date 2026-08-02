# Builder image: produces a Debian or Ubuntu A/B disk image.
# Must be run with --privileged (loop devices, mounts) and an /output volume.
FROM debian:trixie-slim

# The builder runs as the architecture it is building, so debootstrap and the
# chroot execute natively and no emulation is involved. qemu-user-static stays
# for the case where someone does build across architectures deliberately.
RUN apt-get update && apt-get install -y --no-install-recommends \
        debootstrap \
        ubuntu-keyring \
        parted \
        gdisk \
        dosfstools \
        e2fsprogs \
        cryptsetup \
        rsync \
        zstd \
        gzip \
        grub-common \
        ca-certificates \
        qemu-user-static \
        binfmt-support \
        kmod \
    && if [ "$(dpkg --print-architecture)" = amd64 ]; then \
           apt-get install -y --no-install-recommends grub-pc-bin; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# parted shells out to `udevadm settle` after every partition change. No udev
# daemon runs in a container, so the real thing would be a no-op at best — but
# without it parted prints "sh: 1: udevadm: not found" for each call, burying
# the build log in noise. Settling is handled explicitly instead: build-image.sh
# polls sysfs and creates the partition nodes itself. The `udev` package is not
# an option here; it pulls in all of systemd.
RUN printf '#!/bin/sh\nexit 0\n' > /usr/local/sbin/udevadm \
    && chmod +x /usr/local/sbin/udevadm

WORKDIR /build
COPY build-image.sh /build/build-image.sh
COPY overlay /build/overlay
RUN chmod +x /build/build-image.sh

VOLUME /output
ENTRYPOINT ["/build/build-image.sh"]
