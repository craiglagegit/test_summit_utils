#!/bin/bash
# Env wrapper for the DM-56137 mount-motion batch runner.
#
# The non-interactive shell used by Claude Code's Bash tool mis-resolves ~ to
# /home/c/..., which breaks eups and astropy. This wrapper sets the environment
# up correctly in one shell, sets up the local tickets/DM-56137 checkout of
# summit_utils, and then invokes run_mount_batch.py, passing through all args.
#
# Usage:
#   ./run_mount_batch.sh --nights 20260603 20260525
#   ./run_mount_batch.sh --all
set -euo pipefail

REPO_DIR="/sdf/home/c/cslage/claude/test_summit_utils"
SUMMIT_UTILS="${REPO_DIR}/src/WORK/summit_utils"
SCRIPT="${REPO_DIR}/src/notebooks/run_mount_batch.py"

# Load the cached LSST stack env (provides the setup() function + base stack).
# shellcheck source=/dev/null
source "${HOME}/.cache/lsst-env.sh"

# Work around the ~ -> /home/c mis-resolution for eups / numba / astropy caches.
export EUPS_USERDATA="/sdf/home/c/cslage/.eups"
export NUMBA_CACHE_DIR="/tmp/numba_cache_cslage"
export XDG_CACHE_HOME="/sdf/home/c/cslage/.cache"
export MPLCONFIGDIR="/sdf/home/c/cslage/.cache/matplotlib"
mkdir -p "${NUMBA_CACHE_DIR}" "${XDG_CACHE_HOME}" "${MPLCONFIGDIR}"

# Set up the branch we are testing.
setup -j -r "${SUMMIT_UTILS}"

exec python "${SCRIPT}" "$@"
