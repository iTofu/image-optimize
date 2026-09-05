---
name: image-optimize
description: Use when any SVG or PNG file is added to the project — exported from Figma, provided by user, or downloaded from any source. **BLOCKING**: after any file write touches `*.svg` or `*.png` under a project path (especially `.xcassets/`), STOP and invoke this skill before any next action (writing `Contents.json`, referencing the asset in code, committing, etc.). Continuing without running this skill is a violation.
---

# Image Optimize

Optimize SVG and PNG assets for compatibility and performance.

**Assets from Figma MUST be optimized before use** — do not reference unoptimized Figma exports in code. For SVG/PNG from other sources (user-provided, downloaded), ask the user whether they want it optimized before proceeding.

## Workflow

```
New SVG/PNG enters project → Optimize (this skill) → Use in code
```

When using the recommended `-o` approach, the optimization overwrites the original file in-place. File paths remain unchanged — no need to update code references.

## Dependency Check (Do This First)

```bash
python3 -c "import picosvg" 2>/dev/null && echo "picosvg: OK" || echo "picosvg: MISSING - run: pip3 install picosvg"
which pngquant >/dev/null 2>&1 && echo "pngquant: OK" || echo "pngquant: MISSING - run: brew install pngquant (macOS) or apt install pngquant (Linux)"
```

If a dependency is missing, inform the user and offer to install it. SVG and PNG pipelines are independent — a missing dependency for one does not block the other.

## SVG Pipeline

The script `svg_optimize.py` is in this skill's base directory. Use the base directory path provided when this skill is loaded. Recommended usage: **overwrite in-place** (pass `-o` with same path as input).

```bash
# Single file (in-place)
python3 <base-dir>/svg_optimize.py input.svg -o input.svg

# Batch (in-place) — PREFERRED for multi-file runs; preserves sibling viewBox check
python3 <base-dir>/svg_optimize.py *.svg --in-place

# Batch to separate directory (when you need to keep originals)
python3 <base-dir>/svg_optimize.py *.svg --outdir cleaned/

# Skip stroke-to-fill (for decorative illustrations, not icons)
python3 <base-dir>/svg_optimize.py input.svg -o input.svg --no-outline-stroke
```

> ⚠️ **Do NOT batch in-place via a shell `for` loop** (e.g. `for f in *.svg; do ... -o "$f"; done`). Each iteration is a single-file invocation, so the sibling viewBox consistency check (see below) does NOT fire. Use `--in-place` with a glob so the whole batch is processed in one call.

Replace `<base-dir>` with the actual base directory path shown at skill load time.

### What It Fixes

| Problem | SVG Feature | Fix Applied |
|---|---|---|
| Icons blurry/fuzzy; `mask-type:alpha`, `style="mask:…"` and quoted `url('#id')` ignored by CoreSVG | `<mask>` | Every shape under the masked element is intersected with the mask **in place** (own slot, own transform, inherited paint kept) → pure `<path>`; paint order and ancestor transforms are untouched |
| Strokes render wrong | `stroke` attribute (own or inherited from a `<g>`) | Outline stroke → `fill` path; a shape that is filled *and* stroked keeps its fill and gets a sibling stroke path right after it (wrapped in `<g opacity>` when the shape was translucent, so the pair still composites as one layer) |
| Parser failure | `var(--name, #color)` | Resolve to actual color value |
| Parser failure | `width="100%"` | Replace with viewBox dimensions |
| Inconsistent rendering | `<rect>`, `<circle>`, `<ellipse>` | Convert to `<path>` |
| Duplicate elements | `<use>` references | Expand inline |
| File bloat | Invisible elements | Remove |

### What the script refuses to guess about

Constructs that cannot be turned into hard geometry are **left untouched** (the `<mask>` definition
stays, the element keeps its `mask` attribute) and reported on stderr with `[WARN]`; the `[OK]` line
then carries `masks-left:N`. Fix the source in Figma (or flatten by hand) rather than shipping it:

- soft masks: `opacity` / `fill-opacity` (own or inherited, e.g. set on the `<mask>` itself), non
  black-or-white paint inside a luminance `<mask>`, translucent colours (`rgba()` / `#rrggbbaa`)
- `<image>`, `<text>`, nested `<svg>`, `clip-path` / `filter` inside the mask, on the masked element
  itself or anywhere below it; `<use>` is expanded first (picosvg) and only stays unsupported when it
  points outside the file
- a mask nested inside masked content that itself could not be flattened: the outer one is left too,
  so neither mask is lost
- gradient / pattern paint laid out against the shape's bounding box (`gradientUnits` not
  `userSpaceOnUse`, which Figma always sets): the intersection or outline would change the bbox and
  shift the gradient
- `maskContentUnits="objectBoundingBox"`
- dashed strokes (`stroke-dasharray`), `vector-effect` strokes, and stroked shapes carrying a `mask` /
  `clip-path` / `filter` (splitting them into fill + outline paths would apply the effect to each half):
  the stroke is kept
- an ancestor whose `clip-path` / `mask` / `filter` is laid out in objectBoundingBox units (the
  default for mask and filter regions; Figma writes `userSpaceOnUse` explicitly): cutting or outlining
  a descendant would move that effect, so the mask / stroke is kept
- shapes with markers (`marker-start` / `-mid` / `-end`): the generated geometry's vertices are not the
  original path's, so the mask / stroke is kept
- a `<style>` sheet that sets any paint / effect / marker property: the script does not apply CSS selectors, so
  nothing it resolves could be trusted; masks, strokes and unpainted-shape removal are all skipped for
  that file

`paint-order` is honoured both when a shape is split into fill and stroke paths and when mask content
is combined into geometry.

A file that is not valid SVG (or that trips any other error in the pipeline) prints `[FAIL]` and is
skipped with the input untouched; the rest of the batch still runs and the exit code is 1 at the end.
Re-running the script on its own output is a no-op (byte-identical).

### Sibling viewBox Check (batch mode)

When a batch contains 2+ SVG files, the script checks whether all siblings
share the same `viewBox` width and height. If not, it prints a `[WARN]` block
listing the mismatched files.

**Why:** a group of assets rendered side-by-side in UI (e.g. calendar provider
icons, tab bar icons, a list of status badges) must share a common viewBox size
so a single size constant in code renders them consistently. A mismatch means
the wrong Figma layer was exported for at least one of them — typically mixing
a tight inner vector layer with a wrapper frame that carries the intended
padding. Fix by re-exporting all of them from the same-level Figma node.

**Grouping rule** (`_sibling_group_key`): SVGs live in the same group when they
share a parent directory. If the immediate parent is an `.imageset` (iOS asset
catalog), the grouping key is the grandparent (the Xcode Group directory).

**Scope**: the check only compares SVGs **present in the current batch**. It
does NOT scan pre-existing SVGs in the same directory — that would false-positive
on unrelated assets (illustrations next to icon sets, etc). The contract is
simple: to have siblings compared, put them in one invocation.

Disable with `--no-sibling-check` if you intentionally want heterogeneous sizes
(rare; usually indicates the grouping itself is wrong).

## PNG Pipeline

```bash
# Single file (in-place)
pngquant input.png --ext=.png --force --skip-if-larger

# Batch (in-place)
pngquant *.png --ext=.png --force --skip-if-larger
```

`--skip-if-larger` skips already-optimized PNGs.

## Common Mistakes

- **Don't outline strokes on decorative illustrations** — use `--no-outline-stroke` for complex artwork where stroke style matters visually. Outline stroke is designed for icons.
- **Don't skip dependency check** — the script exits with a clear error if picosvg is missing, but checking upfront gives the user a chance to install before processing.
- **SVG with `<filter>` effects** — filters (blur, shadow) cannot be converted to geometry. These must be rasterized or removed. The script does not handle filters.
