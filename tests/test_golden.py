"""Golden tests: every case in tests/cases/ must produce the recorded output, warnings and
stats, and re-optimizing the output must be a byte-identical no-op.

These run anywhere with picosvg installed (seconds, no renderer). Whether the recorded output
is *correct* is established once, by the render harness (tests/run.sh --chrome); from then on
this file guards against the output changing unnoticed.
"""
import os

import pytest

import golden
import svgcmp

CASES = golden.cases()
IDS = [golden.case_name(c) for c in CASES]


@pytest.fixture(scope="module")
def results():
    return {c: golden.run(c) for c in CASES}


def test_cases_present():
    assert len(CASES) >= 60


def test_no_stale_expected():
    stems = {f.rsplit(".", 1)[0] for f in os.listdir(golden.EXPECTED_DIR)}
    assert stems == set(IDS), "tests/expected/ out of sync with tests/cases/; run tests/update_expected.py"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_log_matches_expected(case, results):
    _, log_path = golden.expected_paths(case)
    assert os.path.exists(log_path), "no expected log; run tests/update_expected.py"
    assert results[case].log == open(log_path, encoding="utf-8").read().splitlines()


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_output_matches_expected(case, results):
    r = results[case]
    svg_path, _ = golden.expected_paths(case)
    if r.svg is None:
        assert not os.path.exists(svg_path), "rejected input must not have an expected output"
        return
    assert os.path.exists(svg_path), "no expected output; run tests/update_expected.py"
    diff = svgcmp.first_diff(svgcmp.parse(r.svg), svgcmp.parse(open(svg_path, "rb").read()))
    assert diff is None, diff


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_idempotent(case, results):
    r = results[case]
    if r.svg is None:
        pytest.skip("rejected input")
    again = golden.run_text(r.svg)
    assert again.svg == r.svg, "re-optimizing the output changed it"
    assert again.warnings == r.warnings


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_clean_output_is_pure_geometry(case, results):
    """When nothing was left as-is, the result is paths with fills only: no masks, no strokes,
    no basic shapes, no <use>. This is the whole point of the tool."""
    r = results[case]
    if r.svg is None or r.warnings:
        pytest.skip("input rejected or something intentionally left untouched")
    so = golden.optimizer()
    root = svgcmp.parse(r.svg)
    tags = {svgcmp._tag(e) for e in root.iter() if isinstance(e.tag, str)}
    assert not tags & {"mask", "rect", "circle", "ellipse", "line", "polygon", "polyline", "use"}, tags
    # a <g> may still carry stroke paint for its children to inherit; what matters is that no
    # shape resolves to a stroke
    for path in root.iter(f"{{{so.SVG_NS}}}path"):
        assert so._resolved(path, "stroke", "none") == "none", f"path {path.get('id') or path.get('d')[:30]} still stroked"
