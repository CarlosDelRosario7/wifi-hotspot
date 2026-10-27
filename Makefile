PYTHON ?= python3
PYTHONPATH ?= src

.PHONY: help test lint format run gui doctor build clean check-version deb rpm arch appimage packages install

help:
	@echo "Targets:"
	@echo "  make test          Run unit tests"
	@echo "  make lint          Run ruff and shellcheck"
	@echo "  make format        Format Python sources with ruff"
	@echo "  make run           Run the CLI from the repository"
	@echo "  make gui           Run the GTK4 interface from the repository"
	@echo "  make doctor        Run diagnostics from the repository"
	@echo "  make build         Build a source and wheel distribution"
	@echo "  make check-version Verify that package metadata versions agree"
	@echo "  make deb           Build the Debian package"
	@echo "  make rpm           Build the RPM package"
	@echo "  make arch          Show how to build the Arch package"
	@echo "  make appimage      Build the AppImage"
	@echo "  make packages      Build deb, rpm, and AppImage"
	@echo "  make clean         Remove caches and build artifacts"

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest

lint:
	ruff check src tests
	ruff format --check src tests
	shellcheck wifi.sh \
		packaging/common/version.sh \
		packaging/deb/build.sh packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm \
		packaging/rpm/build.sh \
		packaging/appimage/AppRun packaging/appimage/build.sh
	shellcheck -s bash packaging/arch/PKGBUILD

format:
	ruff format src tests
	ruff check --fix src tests

run:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m wifi_hotspot

gui:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m wifi_hotspot gui

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m wifi_hotspot doctor

build:
	$(PYTHON) -m build

check-version:
	./packaging/check-version.sh

deb:
	./packaging/deb/build.sh

rpm:
	./packaging/rpm/build.sh

arch:
	@echo "En Arch Linux: cd packaging/arch && makepkg -si"

appimage:
	./packaging/appimage/build.sh

packages: deb rpm appimage

install:
	$(PYTHON) -m pip install --break-system-packages .

clean:
	rm -rf build dist .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
