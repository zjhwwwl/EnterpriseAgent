#!/bin/sh
# Run in the Linux Python 3.13.15 image with the hash-locked dev tools installed.
# Constraints preserve shared Windows package versions, not its dependency graph.
set -eu

python -c 'import platform, sys; from importlib.metadata import version; assert sys.platform == "linux" and platform.machine() == "x86_64" and sys.version_info[:3] == (3, 13, 15); assert version("pip-tools") == "7.6.2"'
python -m piptools compile --allow-unsafe --generate-hashes --reuse-hashes --strip-extras \
    --constraint requirements/linux.constraints \
    --output-file requirements/linux-base.lock pyproject.toml
python -m piptools compile --allow-unsafe --extra dev --generate-hashes --reuse-hashes --strip-extras \
    --constraint requirements/linux.constraints \
    --output-file requirements/linux-dev.lock pyproject.toml
