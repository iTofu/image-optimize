#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")/skill" && pwd -P)"
NAME="$(basename "$(cd "$(dirname "$0")" && pwd)")"
DST="$HOME/.claude/skills/$NAME"

if [ ! -L "$DST" ]; then echo "nothing to do: $DST is not a symlink"; exit 0; fi
if [ -e "$DST" ] && [ "$(cd "$DST" && pwd -P)" != "$SRC" ]; then
  echo "refusing: $DST points to $(readlink "$DST"), not to this checkout" >&2; exit 1
fi
rm "$DST"
echo "removed $DST"
