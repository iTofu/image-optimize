#!/usr/bin/env python3
"""Render-diff harness for svg_optimize.py.

Runs the optimizer over tests/cases/*.svg, renders original and optimized files with CoreSVG
(`rendersvg`, the engine behind iOS asset-catalog SVGs, built from render.swift) and optionally
Chrome headless (spec reference), and prints the pixel-difference rate per case. Chrome is the
pass/fail engine: CoreSVG mis-renders several *original* files (mask-type:alpha, style="mask:",
quoted url(), fuzzy mask edges), which is exactly why the optimizer exists.

Columns: core% = CoreSVG original vs optimized; chrome% = Chrome original vs optimized (the check);
x-eng% = Chrome original vs CoreSVG optimized, informational only (meaningless for files without
explicit width/height, where the two engines pick different canvas sizes).

usage: harness.py [--chrome] [--script PATH] [--out DIR] [GLOB]
exit code 1 when any check fails: unexpected crash, chrome diff > 1%, or output not idempotent.
Requires Pillow.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
EXPECT_FAIL = {"r01-empty", "r02-notsvg"}  # invalid input: [FAIL] + exit 1 is the contract
CHROME_TOLERANCE = 1.0  # percent of pixels; stroke outlining costs a little anti-aliasing


def rendersvg_bin():
    path = os.path.join(HERE, "rendersvg")
    if not os.path.exists(path):
        subprocess.run(["swiftc", "-O", os.path.join(HERE, "render.swift"), "-o", path], check=True)
    return path


def core(svg, png):
    r = subprocess.run([rendersvg_bin(), svg, png, "8"], capture_output=True, text=True, errors="replace")
    return r.returncode == 0


def chrome(svg, png, w, h, profile):
    """Chrome headless writes the screenshot and then (on some versions) never exits: wait for the
    file instead of the process, then kill it."""
    if os.path.exists(png):
        os.remove(png)
    p = subprocess.Popen([CHROME, "--headless=new", f"--user-data-dir={profile}", "--disable-gpu",
                          "--hide-scrollbars", "--force-device-scale-factor=8", "--virtual-time-budget=2000",
                          f"--window-size={w},{h}", "--default-background-color=00000000",
                          f"--screenshot={png}", "file://" + os.path.abspath(svg)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        if os.path.exists(png) and os.path.getsize(png) > 0:
            time.sleep(0.5)  # let the write finish
            break
        if p.poll() is not None:
            break
        time.sleep(0.2)
    if p.poll() is None:
        p.kill()
        p.wait()
    return os.path.exists(png) and os.path.getsize(png) > 0


def size(svg):
    s = open(svg, errors="replace").read()
    m = re.search(r'viewBox="[^"]*?([\d.]+) ([\d.]+)"', s)
    if m:
        return int(round(float(m.group(1)))), int(round(float(m.group(2))))
    w = re.search(r'width="([\d.]+)', s)
    h = re.search(r'height="([\d.]+)', s)
    return int(float(w.group(1))), int(float(h.group(1)))


def diff(a, b):
    A = Image.open(a).convert("RGBA")
    B = Image.open(b).convert("RGBA")
    if A.size != B.size:
        B = B.resize(A.size)
    bg = Image.new("RGBA", A.size, (255, 255, 255, 255))
    A = Image.alpha_composite(bg, A).convert("RGB")
    B = Image.alpha_composite(bg, B).convert("RGB")
    d = ImageChops.difference(A, B).convert("L")
    px = d.tobytes()
    return sum(1 for v in px if v > 48) / len(px) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?", default="cases/*.svg")
    ap.add_argument("--script", default=os.path.join(HERE, "..", "skill", "svg_optimize.py"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--chrome", action="store_true", help="also render with Chrome headless (slow, one instance at a time)")
    args = ap.parse_args()

    shutil.rmtree(args.out, ignore_errors=True)
    os.makedirs(f"{args.out}/r", exist_ok=True)
    profile = os.path.join(args.out, "chrome-profile")
    failures = []
    rows = []

    for svg in sorted(glob.glob(os.path.join(HERE, args.pattern))):
        name = os.path.basename(svg)[:-4]
        opt = os.path.abspath(f"{args.out}/{name}.svg")
        r = subprocess.run([sys.executable, args.script, svg, "-o", opt], capture_output=True, text=True, errors="replace")
        warns = [l.strip().replace("[WARN] ", "") for l in r.stderr.splitlines() if "WARN" in l]
        if r.returncode != 0:
            fail_line = next((l.strip() for l in r.stderr.splitlines() if "[FAIL]" in l), r.stderr.strip()[-140:])
            if name in EXPECT_FAIL:
                rows.append((name, "FAIL", "-", "-", "expected", "", fail_line[:110]))
            else:
                failures.append(f"{name}: unexpected failure: {fail_line}")
                rows.append((name, "CRASH", "-", "-", "", "", fail_line[:110]))
            continue
        if name in EXPECT_FAIL:
            failures.append(f"{name}: expected [FAIL], got exit 0")

        # idempotency: optimizing the output again must be byte-identical
        again = f"{args.out}/{name}.again.svg"
        subprocess.run([sys.executable, args.script, opt, "-o", again], capture_output=True)
        if not (os.path.exists(again) and open(again, "rb").read() == open(opt, "rb").read()):
            failures.append(f"{name}: not idempotent")
        if os.path.exists(again):
            os.remove(again)

        w, h = size(svg)
        oc, nc = f"{args.out}/r/{name}-orig-core.png", f"{args.out}/r/{name}-opt-core.png"
        ok_o, ok_n = core(svg, oc), core(opt, nc)
        if not ok_n:
            failures.append(f"{name}: CoreSVG cannot render the optimized file")
        cc = f"{diff(oc, nc):.2f}" if ok_o and ok_n else ("orig-nocore" if not ok_o else "RENDER FAIL")
        chch = chc = "-"
        if args.chrome:
            och, nch = f"{args.out}/r/{name}-orig-chrome.png", f"{args.out}/r/{name}-opt-chrome.png"
            if chrome(svg, och, w, h, profile) and chrome(opt, nch, w, h, profile):
                v = diff(och, nch)
                chch = f"{v:.2f}"
                if v > CHROME_TOLERANCE:
                    failures.append(f"{name}: chrome diff {v:.2f}% > {CHROME_TOLERANCE}%")
                chc = f"{diff(och, nc):.2f}" if ok_n else "-"
            else:
                failures.append(f"{name}: chrome render failed")
        stat = [l for l in r.stdout.splitlines() if "[OK]" in l]
        stat = stat[0].split("  ")[-1].replace("size:", "") if stat else ""
        txt = open(opt).read()
        flags = ("M" if "<mask" in txt else "") + ("S" if re.search(r'stroke[=:](?!"none")', txt) else "")
        rows.append((name, cc, chch, chc, stat, flags, " | ".join(warns)[:110]))

    print(f"{'case':44} {'core%':11} {'chrome%':8} {'x-eng%':7} {'stats':34} {'left':5} warnings")
    for r in rows:
        print(f"{r[0]:44} {r[1]:11} {r[2]:8} {r[3]:7} {r[4]:34} {r[5]:5} {r[6]}")
    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"ok: {len(rows)} cases, no unexpected failures, all outputs idempotent"
          + (", chrome diff within tolerance" if args.chrome else " (CoreSVG only; add --chrome for the spec engine)"))


if __name__ == "__main__":
    main()
