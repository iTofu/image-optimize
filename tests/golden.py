"""Shared plumbing for the golden tests: run the optimizer on one case in-process and capture
everything the contract promises (output SVG, stats line, [WARN] lines, or the failure type).

expected/<case>.svg  the optimized output (compared structurally, see svgcmp.py)
expected/<case>.log  one stats line, then one `WARN: …` line per warning;
                     `FAIL: <ExceptionType>` alone for inputs the script must reject
"""
import glob
import importlib.util
import os
import sys
import tempfile
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skill", "svg_optimize.py")
CASES_DIR = os.path.join(HERE, "cases")
EXPECTED_DIR = os.path.join(HERE, "expected")

_module = None


def optimizer():
    """The script imported as a module (once)."""
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location("svg_optimize", SCRIPT)
        _module = importlib.util.module_from_spec(spec)
        sys.modules["svg_optimize"] = _module
        spec.loader.exec_module(_module)
    return _module


def cases():
    return sorted(glob.glob(os.path.join(CASES_DIR, "*.svg")))


def case_name(path: str) -> str:
    return os.path.basename(path)[:-4]


@dataclass
class Result:
    svg: str | None            # optimized output, None when the script rejected the input
    log: list[str] = field(default_factory=list)
    error: Exception | None = None

    @property
    def warnings(self):
        return [l[6:] for l in self.log if l.startswith("WARN: ")]


def run(src: str, outline: bool = True) -> Result:
    so = optimizer()
    warns = []
    keep = so._warn
    so._warn = warns.append
    try:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "out.svg")
            try:
                stats = so.optimize_svg(src, out, outline=outline)
            except Exception as e:  # the CLI turns any failure into [FAIL]; the type is the contract
                return Result(None, [f"FAIL: {type(e).__name__}"], e)
            svg = open(out, encoding="utf-8").read()
    finally:
        so._warn = keep
    log = [f"masks:{stats['masks']} masks-left:{stats.get('masks_skipped', 0)} strokes:{stats['strokes']}"]
    log += [f"WARN: {w}" for w in warns]
    return Result(svg, log)


def run_text(svg_text: str, outline: bool = True) -> Result:
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.svg")
        open(src, "w", encoding="utf-8").write(svg_text)
        return run(src, outline=outline)


def expected_paths(case: str):
    name = case_name(case)
    return os.path.join(EXPECTED_DIR, name + ".svg"), os.path.join(EXPECTED_DIR, name + ".log")


def write_expected(case: str, r: Result) -> None:
    svg_path, log_path = expected_paths(case)
    os.makedirs(EXPECTED_DIR, exist_ok=True)
    open(log_path, "w", encoding="utf-8").write("\n".join(r.log) + "\n")
    if r.svg is None:
        if os.path.exists(svg_path):
            os.remove(svg_path)
    else:
        open(svg_path, "w", encoding="utf-8").write(r.svg)
