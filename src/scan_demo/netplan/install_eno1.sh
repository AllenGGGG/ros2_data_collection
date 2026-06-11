#!/usr/bin/env bash
# Backward-compatible entry point. The scanner may be attached through a USB hub,
# so the maintained installer no longer assumes eno1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/install_matrix220_network.sh" "$@"
