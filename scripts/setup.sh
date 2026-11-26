#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if ! command -v deno >/dev/null && ! command -v node >/dev/null; then
  echo 'YouTube 链接需要 Deno 2.3+ 或 Node 22+。上传音频转录无需 JS 运行时。' >&2
fi
.venv/bin/python -c 'import ctranslate2, faster_whisper, yt_dlp, yt_dlp_ejs; print("dependencies imported successfully")'
.venv/bin/python -m unittest discover -s tests -v
echo 'Setup complete. Model is downloaded on first transcription, not during setup.'
