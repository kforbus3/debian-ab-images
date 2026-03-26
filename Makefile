# Makefile for Debian A/B Image System

# Default target
.PHONY: all
all: build

# Configuration
BUILD_DIR := build-scripts
CERTS_DIR := certs
BUNDLES_DIR := bundles

# Colors
GREEN := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
RESET := $(shell tput -Txterm sgr0)

# Targets
.PHONY: build
build:
	@echo "$(GREEN)Building Debian A/B image...$(RESET)"
	$(BUILD_DIR)/build-debian-ab.sh

.PHONY: build-encrypted
build-encrypted:
	@echo "$(GREEN)Building encrypted Debian A/B image...$(RESET)"
	$(BUILD_DIR)/build-debian-ab.sh -e

.PHONY: certs
certs:
	@echo "$(GREEN)Generating RAUC certificates...$(RESET)"
	$(BUILD_DIR)/generate-rauc-certs.sh

.PHONY: clean
clean:
	@echo "$(YELLOW)Cleaning up...$(RESET)"
	rm -f *.img
	rm -rf $(CERTS_DIR)
	rm -rf $(BUNDLES_DIR)

.PHONY: help
help:
	@echo "Debian A/B Image System Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  build           Build standard Debian A/B image"
	@echo "  build-encrypted Build encrypted Debian A/B image"
	@echo "  certs           Generate RAUC certificates"
	@echo "  clean           Remove generated images and certificates"
	@echo "  help            Show this help message"
	@echo ""
	@echo "For more options, run the scripts directly:"
	@echo "  $(BUILD_DIR)/build-debian-ab.sh --help"

# Document targets
$(BUILD_DIR)/build-debian-ab.sh:
	@echo "$(YELLOW)Build script not found. Please check the build-scripts directory.$(RESET)"

$(BUILD_DIR)/generate-rauc-certs.sh:
	@echo "$(YELLOW)Certificate generation script not found. Please check the build-scripts directory.$(RESET)"