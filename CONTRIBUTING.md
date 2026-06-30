# Contributing to Debian A/B Images

Thanks for your interest in improving this project!

## Reporting issues

- Search existing issues first.
- Include your host OS, Docker version, target hardware/VM, and the relevant logs
  (build log, `docker compose logs`, or imager serial output).
- **Never** include private keys, real passwords, or built images in an issue.

## Project layout

```
builder/   Docker build → bootable Debian A/B image
imager/    Docker build → netboot kernel + initramfs
server/    docker compose → dnsmasq (PXE/DHCP) + nginx (HTTP)
docs/      Architecture, deployment, builder, updates, security
scripts/   Helpers (e.g. QEMU end-to-end test)
```

## Developing & testing

The builder and imager run in Docker (`--privileged` for the builder). You can
validate changes entirely in QEMU without hardware — see the QEMU recipe in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), or `scripts/test-imager-e2e.sh` for the
full image → netboot-image → boot loop.

```bash
make image        # build an image
make imager       # build the netboot imager
```

## Pull requests

1. Keep changes focused; describe what changed and why.
2. Keep shell scripts `set -euo pipefail`-clean and POSIX/bash-portable.
3. Update the docs when behavior, options, or layout change.
4. Verify a build still boots (QEMU) before submitting.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
