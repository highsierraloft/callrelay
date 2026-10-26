#!/usr/bin/env bash
# Shared verification entrypoint for contributors and CI.
#
# Every gate lives here so local verification does not drift from CI
# (missing lint scope, missing coverage floor, extra unversioned steps). Run it
# from a clean, credential-free environment. It never places a phone call,
# contacts a provider, or touches production state.
#
# Usage: bash scripts/verify.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run --extra android --frozen ruff format --check app tests scripts
uv run --extra android --frozen ruff check app tests scripts
uv run --extra android --frozen mypy app
uv run --extra android --frozen pytest -q --cov=app
