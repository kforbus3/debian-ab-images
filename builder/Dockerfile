# Builder image: produces a Debian A/B disk image.
# Must be run with --privileged (loop devices, mounts) and an /output volume.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        debootstrap \
        parted \
        gdisk \
        dosfstools \
        e2fsprogs \
        rsync \
        zstd \
        gzip \
        grub-pc-bin \
        grub-common \
        ca-certificates \
        qemu-user-static \
        binfmt-support \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY build-image.sh /build/build-image.sh
COPY overlay /build/overlay
RUN chmod +x /build/build-image.sh

VOLUME /output
ENTRYPOINT ["/build/build-image.sh"]
