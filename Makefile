# Debian A/B Images — build & provisioning orchestration.
.DEFAULT_GOAL := help
OUTPUT ?= $(CURDIR)/output

# Image build options (override on the command line, e.g. `make image HOSTNAME=web01`)
# SUITE picks the release; the distro is auto-detected from it
# (trixie/bookworm -> Debian, noble/jammy -> Ubuntu).
SUITE ?= trixie
HOSTNAME ?=
USERNAME ?= debian
PASSWORD ?= debian
IMAGE_SIZE ?= 8
ROOT_SIZE ?= 3072
COMPRESS ?= zstd

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: image
image: ## Build the A/B disk image into ./output (SUITE=trixie|bookworm|noble|jammy)
	./builder/run.sh --suite $(SUITE) $(if $(HOSTNAME),--hostname $(HOSTNAME)) \
	  --username $(USERNAME) --password '$(PASSWORD)' \
	  --image-size $(IMAGE_SIZE) --root-size $(ROOT_SIZE) --compress $(COMPRESS)

.PHONY: imager
imager: ## Build the netboot imager (kernel + initramfs) into ./output/imager
	./imager/run.sh

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
