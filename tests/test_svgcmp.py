"""The comparator itself: what counts as the same SVG, and the directory mode of the CLI."""
import subprocess
import sys

import svgcmp

SVG = '<svg xmlns="http://www.w3.org/2000/svg">%s</svg>'


def same(a, b):
    return svgcmp.first_diff(svgcmp.parse(SVG % a), svgcmp.parse(SVG % b))


def test_attribute_order_whitespace_and_comments_are_noise():
    assert same('<path d="M0 0" fill="red"/>', '<!-- c --><path fill="red"  d="M0 0" />') is None


def test_numbers_within_tolerance_are_equal():
    assert same('<path d="M10.0000,6.0000 L30,6"/>', '<path d="M10.0005,6 L30,6"/>') is None
    assert "d=" in same('<path d="M10.0000,6"/>', '<path d="M10.002,6"/>')


def test_real_differences_are_located():
    assert "fill='red' vs 'blue'" in same('<path fill="red"/>', '<path fill="blue"/>')
    assert "only on left" in same('<path id="a"/>', "<path/>")
    assert "children" in same("<g><path/></g>", "<g><path/><path/></g>")
    assert "<path> vs <g>" in same("<path/>", "<g/>")
    assert "text" in same("<style>a{}</style>", "<style>b{}</style>")


def cli(*args):
    return subprocess.run([sys.executable, svgcmp.__file__, *args], capture_output=True, text=True)


def test_cli_directory_mode_reports_files_missing_on_either_side(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    for d in (a, b):
        (d / "shared.svg").write_text(SVG % '<path d="M0 0"/>')
    (a / "left-only.svg").write_text(SVG % "<path/>")
    (b / "right-only.svg").write_text(SVG % "<path/>")
    r = cli(str(a), str(b))
    assert r.returncode == 1
    assert "MISSING" in r.stdout and "left-only.svg" in r.stdout and "right-only.svg" in r.stdout
    assert "1 same, 2 differ" in r.stdout


def test_cli_identical_directories_exit_zero(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "x.svg").write_text(SVG % '<path d="M0 0" fill="red"/>')
    (b / "x.svg").write_text(SVG % '<path fill="red" d="M0 0"/>')
    r = cli(str(a), str(b))
    assert r.returncode == 0 and "1 same, 0 differ" in r.stdout


def test_cli_single_files(tmp_path):
    x, y = tmp_path / "x.svg", tmp_path / "y.svg"
    x.write_text(SVG % '<path fill="red"/>')
    y.write_text(SVG % '<path fill="blue"/>')
    r = cli(str(x), str(y))
    assert r.returncode == 1 and "DIFF x.svg" in r.stdout
    assert cli(str(x), str(x)).returncode == 0
    assert cli(str(x)).returncode == 2
