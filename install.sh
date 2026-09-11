#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Reelsi — macOS and Linux installation.
#
#   bash reelsi/install.sh [--cpu] [--no-optional]
#
# This script does NOT install ffmpeg or Python: they are system-level
# dependencies and pulling them silently from the internet is not appropriate.
# It tells you what is missing and how to install it.
#
# The main reason this script exists: correct torch build. A plain
# `pip install torch` on Linux with an AMD GPU installs the CPU build,
# and all GPU computation becomes orders of magnitude slower — silently,
# with no errors.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE_CPU=0
NO_OPTIONAL=0
for arg in "$@"; do
  case "$arg" in
    --cpu) FORCE_CPU=1 ;;
    --no-optional) NO_OPTIONAL=1 ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done

say()  { echo "  $*"; }
step() { echo; echo "== $*"; }
bad()  { echo "  ERROR: $*" >&2; }

PY=${PYTHON:-python3}
OS="$(uname -s)"

step "Checking environment"

command -v "$PY" >/dev/null || { bad "$PY not found"; exit 1; }
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "Python $PYVER ($OS)"
[ "$PYVER" = "3.10" ] || say "WARNING: project targets 3.10; some wheels may not be available for other versions."

if command -v ffmpeg >/dev/null; then
  say "ffmpeg found"
else
  bad "ffmpeg not found in PATH — no audio or rendering without it."
  if [ "$OS" = "Darwin" ]; then say "Install: brew install ffmpeg"
  else say "Install: sudo apt install ffmpeg  (or your distro's package manager)"; fi
  exit 1
fi

if [ "$OS" = "Linux" ]; then
  say "NOTE: Adobe is not available on Linux — After Effects builds are not possible."
  say "Cutting and subtitles work; FCP7 XML opens in DaVinci Resolve."
fi

step "Installing torch"

if [ "$FORCE_CPU" = "1" ]; then
  say "CPU build (forced via --cpu flag)"
  "$PY" -m pip install torch torchaudio torchvision
elif [ "$OS" = "Darwin" ]; then
  # On Apple Silicon the default PyPI wheel already includes Metal (MPS) —
  # no separate index needed, unlike CUDA and ROCm.
  say "macOS — default build, MPS (Metal) is included"
  "$PY" -m pip install torch torchaudio torchvision
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  say "NVIDIA detected — installing CUDA 12.1 build"
  "$PY" -m pip install torch==2.5.1 torchaudio==2.5.1 torchvision==0.20.1 \
      --index-url https://download.pytorch.org/whl/cu121
elif command -v rocminfo >/dev/null 2>&1 || [ -d /opt/rocm ]; then
  say "ROCm detected — installing AMD build"
  say "If your ROCm version differs, install torch manually: https://pytorch.org/get-started/locally/"
  "$PY" -m pip install torch torchaudio torchvision \
      --index-url https://download.pytorch.org/whl/rocm6.2
else
  say "GPU not detected — CPU build. Everything will work, but orders of magnitude slower."
  "$PY" -m pip install torch torchaudio torchvision
fi

step "Installing dependencies"
"$PY" -m pip install -r "$HERE/requirements.txt"

if [ "$NO_OPTIONAL" = "0" ]; then
  step "Optional dependencies (skip with --no-optional)"
  say "Each one disables a single feature; errors here are not fatal."
  # set +e intentionally: gigaam pulls from git and fails where git is not installed.
  # That is not a reason to abort installation.
  set +e
  "$PY" -m pip install -r "$HERE/requirements-optional.txt"
  set -e
fi

step "Verification"
"$PY" "$HERE/doctor.py" || true
# bootstrap переехал в пакет core/: из корня он запускается как модуль, а не по пути
# к файлу — иначе `core.paths` не найдётся и личные файлы заведутся не там.
(cd "$HERE" && "$PY" -m core.bootstrap) || true

echo
echo "Run:  $PY reelsi/webui.py"
