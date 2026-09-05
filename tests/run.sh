#!/usr/bin/env bash
# Regression entry point. Renders with CoreSVG only by default (fast, seconds); add --chrome to also run
# headless Chrome for the two-engine comparison (about 7 s per case).
# Needs python3 + picosvg + Pillow and swiftc (compiles rendersvg on first run); --chrome needs Google Chrome.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/harness.py" "$@"
