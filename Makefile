ifeq ($(OS),Windows_NT)
    PLUGIN_DIR := $(APPDATA)/nicotine/plugins/audiophile_wishlist
    MKDIR = if not exist "$(PLUGIN_DIR)" mkdir "$(PLUGIN_DIR)"
    CP = copy /Y
    SEP = \\
    PYTHON = python
else
    PLUGIN_DIR := $(HOME)/.local/share/nicotine/plugins/audiophile_wishlist
    MKDIR = mkdir -p $(PLUGIN_DIR)
    CP = cp
    SEP = /
    PYTHON = python3
endif

.PHONY: install test lint format clean

## install : Copy plugin to Nicotine+ plugins folder (auto-detects Mac/Linux/Windows)
install:
	@$(MKDIR)
	$(CP) audiophile_wishlist$(SEP)__init__.py "$(PLUGIN_DIR)$(SEP)"
	$(CP) audiophile_wishlist$(SEP)PLUGININFO "$(PLUGIN_DIR)$(SEP)"
	@echo "Installed to $(PLUGIN_DIR)"
	@echo "  Reopen Preferences > Plugins in Nicotine+ to reload"

## test : Run unit tests
test:
	$(PYTHON) -m pytest tests/ -v

## lint : Run ruff linter
lint:
	ruff check audiophile_wishlist/ tests/

## format : Auto-format with ruff
format:
	ruff format audiophile_wishlist/ tests/
	ruff check --fix audiophile_wishlist/ tests/

## clean : Remove caches
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache

## help : Show this help
help:
	@sed -n 's/^## //p' Makefile
