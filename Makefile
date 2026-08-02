# Debian A/B Images — build & provisioning orchestration.
.DEFAULT_GOAL := help
OUTPUT ?= $(CURDIR)/output

# Image build options (override on the command line, e.g. `make image HOSTNAME=web01`)
# SUITE picks the release; the distro is auto-detected from it
# (trixie/bookworm -> Debian, resolute/noble/jammy -> Ubuntu).
SUITE ?= trixie
HOSTNAME ?=
USERNAME ?= debian
PASSWORD ?= debian
# "auto" = smallest possible image; it expands to fill the disk on first boot
IMAGE_SIZE ?= auto
ROOT_SIZE ?= 3072
COMPRESS ?= zstd
# Extra packages to install into the image, space-separated
# (e.g. `make image PACKAGES="vim curl qemu-guest-agent"`)
PACKAGES ?=
# LUKS2 encryption: ENCRYPT=1 enables it; UNLOCK picks the method
# (passphrase|keyfile|tpm2|tang); LUKS_PASSPHRASE is required with ENCRYPT=1
ENCRYPT ?=
UNLOCK ?= tpm2
LUKS_PASSPHRASE ?=
TANG_URL ?=

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: image
image: ## Build the A/B disk image into ./output (SUITE=trixie|bookworm|noble|jammy, PACKAGES="vim curl", ENCRYPT=1)
	./builder/run.sh --suite $(SUITE) $(if $(HOSTNAME),--hostname $(HOSTNAME)) \
	  --username $(USERNAME) --password '$(PASSWORD)' \
	  --image-size $(IMAGE_SIZE) --root-size $(ROOT_SIZE) --compress $(COMPRESS) \
	  $(if $(PACKAGES),--packages "$(PACKAGES)") \
	  $(if $(ENCRYPT),--encrypt --unlock $(UNLOCK) \
	    $(if $(LUKS_PASSPHRASE),--luks-passphrase '$(LUKS_PASSPHRASE)') \
	    $(if $(TANG_URL),--tang-url $(TANG_URL)))

.PHONY: imager
imager: ## Build the netboot imager (kernel + initramfs) into ./output/imager
	./imager/run.sh

.PHONY: webui
webui: ## Start the web management UI on http://localhost:8080 (needs webui/.env)
	@test -f webui/.env || { \
	  echo "webui/.env is missing. Create it with:"; \
	  echo "  cp webui/.env.example webui/.env"; \
	  echo "  # then set ADMIN_PASSWORD and SECRET_KEY"; exit 1; }
	cd webui && docker compose up -d --build
	@echo "Web UI: http://localhost:8080"

.PHONY: webui-down
webui-down: ## Stop the web management UI
	cd webui && docker compose down

.PHONY: webui-logs
webui-logs: ## Follow web UI logs
	cd webui && docker compose logs -f

.PHONY: server-up
server-up: ## Start the PXE/HTTP provisioning server (needs server/.env)
	cd server && docker compose up -d --build

.PHONY: server-down
server-down: ## Stop the provisioning server
	cd server && docker compose down

.PHONY: server-logs
server-logs: ## Follow provisioning server logs
	cd server && docker compose logs -f

.PHONY: all
all: image imager ## Build both the A/B image and the imager

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf $(OUTPUT)
