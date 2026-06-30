# Imager builder: produces the netboot kernel + initramfs.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        linux-image-amd64 \
        busybox-static \
        zstd \
        xz-utils \
        cpio \
        gzip \
        kmod \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /imager
COPY build-imager.sh init udhcpc.script /imager/
RUN chmod +x /imager/build-imager.sh

VOLUME /output
ENTRYPOINT ["/imager/build-imager.sh"]
