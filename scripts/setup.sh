#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import ctranslate2, faster_whisper, yt_dlp; print("dependencies imported successfully")'
.venv/bin/python -m unittest discover -s tests -v
echo 'Setup complete. Model is downloaded on first transcription, not during setup.'
