#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")/skill" && pwd)"
NAME="$(basename "$(cd "$(dirname "$0")" && pwd)")"
DST="$HOME/.claude/skills/$NAME"

# Dependency check: report only, never install (that is the user's call). The SVG and PNG pipelines are independent.
missing=0
python3 -c "import picosvg, pathops, lxml" >/dev/null 2>&1 || { echo "warning: picosvg missing (SVG pipeline): pip3 install picosvg"; missing=1; }
command -v pngquant >/dev/null || { echo "warning: pngquant missing (PNG pipeline): brew install pngquant"; missing=1; }
if [ "$missing" -eq 1 ]; then echo "Missing dependencies do not block the install; the affected pipeline exits with an error at run time."; fi

if [ -e "$DST" ] && [ ! -L "$DST" ]; then echo "refusing: $DST exists and is not a symlink" >&2; exit 1; fi
mkdir -p "$HOME/.claude/skills"
ln -sfn "$SRC" "$DST"
echo "linked $DST -> $SRC"
/bin/ls -l "$DST"
