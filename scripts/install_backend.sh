#!/usr/bin/env bash
# Install backend dependencies so that OCR actually works.
#
# Why this is a script and not just `pip install -r requirements.txt`:
#
# rapidocr-onnxruntime depends on `opencv-python`, whose binary links against
# libGL.so.1. Trimmed server images (python:3.11-slim, Render's free tier, the
# CI runner) do not ship libGL, so on those a plain install produces a cv2 that
# raises ImportError("libGL.so.1") - and OCR is dead while everything else looks
# fine. `opencv-python-headless` is the same library without the GUI/library
# dependencies, but the two distributions ship an identical `cv2/cv2.abi3.so`,
# so whichever is installed last wins.
#
# Reinstalling headless last, with --no-deps so it cannot drag the GUI build
# back in, puts a working cv2 on disk. Verified on a clean venv: without the
# second step `import cv2` fails; with it, cv2 5.1.0 imports and reads a scan.
set -euo pipefail

PYTHON="${PYTHON:-python3}"
REQ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Debian/Ubuntu mark the system Python as externally managed (PEP 668) and pip
# then refuses to install into it. Containers and CI use a plain Python and are
# unaffected; this only makes the script work on a developer machine that uses
# the distro Python, where the alternative is a confusing hard failure.
PIP_FLAGS=()
_MARKER="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/EXTERNALLY-MANAGED"
# Test for existence, not contents: on Debian the marker is root-owned and an
# unreadable-file grep exits 2, which reads the same as "not found".
if [ -e "$_MARKER" ]; then
  PIP_FLAGS+=(--break-system-packages)
fi

"$PYTHON" -m pip install "${PIP_FLAGS[@]}" -r "$REQ_DIR/backend/requirements.txt"

# The step that decides whether OCR works. Cheap when already satisfied.
"$PYTHON" -m pip install "${PIP_FLAGS[@]}" --force-reinstall --no-deps --only-binary=:all: \
    opencv-python-headless==5.1.0.95

# Fail the build here rather than at upload time, so a broken OCR deploy is
# caught during install instead of by a tenant photographing their lease.
"$PYTHON" - <<'CHECK'
import sys
try:
    import cv2
except Exception as exc:
    sys.exit(f"cv2 is broken after install: {exc}. OCR would fail at runtime.")

try:
    import rapidocr_onnxruntime  # noqa: F401
except Exception as exc:
    sys.exit(f"rapidocr cannot import: {exc}. OCR would fail at runtime.")

print(f"OCR ready (cv2 {cv2.__version__})")
CHECK
