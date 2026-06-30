FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        dnsmasq ipxe \
    && rm -rf /var/lib/apt/lists/*

# Stage the iPXE network bootloaders into the TFTP root.
RUN mkdir -p /srv/tftp \
    && cp /usr/lib/ipxe/undionly.kpxe /srv/tftp/ \
    && cp /usr/lib/ipxe/ipxe.efi      /srv/tftp/ \
    && cp /usr/lib/ipxe/snponly.efi   /srv/tftp/ 2>/dev/null || true

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
