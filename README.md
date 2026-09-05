# image-optimize

English | [简体中文](translations/README.zh.md)

Turns SVG and PNG assets exported from Figma and similar tools into a form every renderer draws correctly, above all CoreSVG, the engine behind iOS asset catalogs. Installed as a [Claude Code](https://claude.com/claude-code) skill, the agent runs it whenever an asset lands in a project; `svg_optimize.py` also works on its own as a command-line tool.

## Why

CoreSVG (the engine Xcode asset catalogs use to render SVG) supports only a subset of SVG: it ignores `mask-type:alpha`, `style="mask:…"` and quoted `url('#id')` references, blurs mask edges, and occasionally draws strokes wrong. Figma exports lean on exactly these constructs, so the design looks fine while on the device the icon is blurry, has a hole, or does not show at all.

This tool turns those constructs into plain geometry ahead of time:

| Input | What happens |
|---|---|
| `<mask>` (hard mask) | Every shape under the mask is intersected with it in the shape's own coordinate system and **replaced in place** by a `<path>`: z-order, ancestor transforms, group opacity and inherited fill all survive |
| `stroke` (including one inherited from a `<g>`) | The stroke is outlined into a filled `<path>`; a shape with both fill and stroke keeps its fill and gets a stroke path right after it (wrapped in `<g opacity>` if it was translucent, so the composite is unchanged) |
| `var(--name, #color)` | Resolved to the actual colour |
| `width="100%"` / `height="100%"` | Replaced by the viewBox size |
| `<rect>` `<circle>` `<ellipse>` `<line>` `<polygon>` `<polyline>` | Converted to `<path>`, other attributes kept |
| `<use>` | Expanded in place (including SVG 2 `href`) |
| Elements that paint nothing | Removed |

The result is nothing but `<path>` plus `fill`, which every engine renders the same way. Running the tool on its own output changes no bytes.

## What it refuses to guess

Anything that cannot become hard geometry is **left untouched** (the `<mask>` definition stays, the element keeps its `mask` attribute), a `[WARN]` goes to stderr and the `[OK]` line carries `masks-left:N`. That is the moment to fix the design source rather than let a tool guess:

- Soft masks: mask content with `opacity` / `fill-opacity` (inherited included), fills that are not pure black or white, colours with alpha
- `<image>`, `<text>`, nested `<svg>`, `clip-path` or `filter` inside the mask or the masked content; `<use>` is kept only when it points outside the file
- An inner mask that cannot be flattened: the outer one is kept whole too, nothing is dropped
- Gradient or pattern fills laid out against the bounding box (`gradientUnits` other than `userSpaceOnUse`; Figma always exports `userSpaceOnUse`)
- `maskContentUnits="objectBoundingBox"`
- Dashed strokes, `vector-effect` strokes, stroked shapes carrying `mask` / `clip-path` / `filter`: the stroke stays a stroke
- An ancestor `clip-path` / `mask` / `filter` laid out against the bounding box (the default unit for mask and filter regions; Figma writes `userSpaceOnUse` explicitly): changing descendant geometry would shift it, so nothing moves
- Shapes with markers: the new geometry no longer has the original vertices, so they are left alone
- A `<style>` sheet that sets any paint, effect or marker property: the script does no CSS selector matching, so masks and strokes in the whole file are left alone

Invalid files (not SVG, parse failure, any exception in the pipeline) print `[FAIL]` and are skipped with the input untouched; the rest of the batch continues and the exit code is 1.

## Install

Dependencies: Python 3 with [picosvg](https://github.com/googlefonts/picosvg) for the SVG pipeline and [pngquant](https://pngquant.org/) for the PNG pipeline. Either pipeline works without the other.

```bash
pip3 install picosvg
brew install pngquant

git clone https://github.com/iTofu/image-optimize.git
cd image-optimize && ./install.sh
```

`install.sh` symlinks `skill/` to `~/.claude/skills/image-optimize` (it refuses to overwrite an existing path that is not a symlink) and checks the dependencies, reporting what is missing without installing anything. `git pull` in the clone upgrades the skill in place; `./uninstall.sh` removes the symlink.

You do not need Claude Code to use it: `skill/svg_optimize.py` is a self-contained single-file script.

## Usage

### As a Claude Code skill

Nothing to trigger by hand. The skill description tells the agent to run the optimizer after any `*.svg` / `*.png` is written under a project path (especially `.xcassets/`) and before the file is referenced, so icons pulled from Figma, images dropped in by the user and downloaded assets all go through it. The rules live in `skill/SKILL.md`.

### Command line

```bash
S=~/.claude/skills/image-optimize/svg_optimize.py

python3 $S icon.svg -o icon.svg              # single file, overwrite in place
python3 $S *.svg --in-place                  # batch, in place (recommended)
python3 $S *.svg --outdir cleaned/           # keep the originals
python3 $S icon.svg -o icon.svg --no-outline-stroke   # decorative artwork: keep strokes as strokes

pngquant *.png --ext=.png --force --skip-if-larger
```

Batch mode checks that the SVGs in one directory (for an iOS asset catalog, the directory above the `.imageset`) share the same viewBox size: icons displayed side by side should share one size, and a mismatch usually means one of them was exported from the wrong Figma layer. `--no-sibling-check` turns this off.

One line per file:

```
[OK] icon.svg -> icon.svg  masks:2 strokes:3 size:1532→1210B
[OK] badge.svg -> badge.svg  masks:0 masks-left:1 strokes:0 size:804→806B
[FAIL] broken.svg: XMLSyntaxError: Document is empty, line 1, column 1
```

## Tests

Two tiers. The first needs no renderer, runs in seconds on any machine and is required for every PR in CI; the second asks real rendering engines whether the picture is still the same and runs locally.

### Fast tier (pytest)

```bash
pip3 install -r tests/requirements.txt
pytest
```

- `tests/test_golden.py`: for every case in `tests/cases/` the output, the `[WARN]` lines and the statistics line must match what `tests/expected/` records (structural comparison that ignores attribute order and formatting, with a numeric tolerance of 0.001); running the tool on its own output must change no bytes; a case with no WARN must leave no `<mask>`, basic shape, `<use>` or stroke in the output.
- `tests/test_cli.py`: the command-line contract: exit codes, `[OK]` / `[FAIL]` / `[SKIP]` lines, a batch that keeps going after a failure, the sibling viewBox check.
- `tests/test_units.py`: the pure functions that decide whether to touch something: colour alpha, paint-order, markers, bounding-box clip / mask / filter, stylesheet property collection, CSS variables, dimension fixes.

When a change to the output is intended, run `python3 tests/update_expected.py` (optionally with case names to update only some) and put the `tests/expected/` diff in the PR: that diff is the behaviour change. `tests/requirements.txt` pins picosvg and skia-pathops, because otherwise the last digits of the path boolean operations drift between machines and the expected files with them.

`tests/svgcmp.py` also works on its own to compare two files or two directories: `python3 tests/svgcmp.py a/ b/`.

### Render tier (harness)

```bash
tests/run.sh            # CoreSVG only, seconds: no unexpected crash, idempotent output
tests/run.sh --chrome   # adds headless Chrome for a two-engine pixel comparison, about 7 s per case
```

The harness renders the original and the optimized file with each engine and computes the pixel difference. Chrome is the judge (more than 1 % difference between original and optimized fails the case); CoreSVG is reference only, because the cases it draws wrong in the *original* are the reason this tool exists. `tests/rendersvg` is compiled from `tests/render.swift` on first run and needs the Xcode command-line tools; `--chrome` needs Google Chrome installed. Run only one headless Chrome harness at a time.

### Cases

`tests/cases/` is grouped by prefix:

- `c*` mask-flattening structures: z-order, transforms, inherited style, nested masks, evenodd, shared masks, units, mask region clipping
- `r*` robustness inputs: empty file, not SVG, no viewBox, huge coordinates, comments and processing instructions, nested svg
- `x*` / `y*` / `z*` / `w*` edge cases: inherited stroke, translucent fill plus stroke, bounding-box gradients, inner soft mask, clip-path / filter on the mask host, zero-width stroke, mask inheriting opacity, ancestor bounding-box effects, paint-order, markers, stylesheets

A newly found rendering bug starts as a minimal SVG case that reproduces it (red under `--chrome`), then the fix, then `update_expected.py` to record the output.

## License

[Apache License 2.0](LICENSE).
