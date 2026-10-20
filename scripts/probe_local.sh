#!/usr/bin/env bash
# Run the XBRL coverage probe from your own machine and leave nothing behind.
#
# Everything (repo copy, virtualenv, downloaded packages) lives in one temporary
# directory that is deleted on exit, even after an error or Ctrl-C. pip runs
# without a cache. The only output is the table printed to the terminal.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/IgnacioMendozaRojas/Chile-clinics-benchmark/main/scripts/probe_local.sh)
#   bash <(curl -fsSL .../probe_local.sh) --quick
#
# Arguments are passed through to `python -m clinics.probe` (see --help).
set -euo pipefail

REPO="https://github.com/IgnacioMendozaRojas/Chile-clinics-benchmark.git"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/clinics-probe.XXXXXX")"
cleanup() { rm -rf "$WORK"; echo "Cleaned up $WORK" >&2; }
trap cleanup EXIT INT TERM

PY="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11+ is required (found: ${PY:-none})." >&2
  exit 1
fi

export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
git clone --quiet --depth 1 "$REPO" "$WORK/repo"
"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --quiet "$WORK/repo"
cd "$WORK"
"$WORK/venv/bin/python" -m clinics.probe "$@"
