"""The command-line contract: exit codes, [OK] / [FAIL] lines, batch behaviour and the sibling
viewBox check. These run the script as a subprocess, exactly as the skill and users do."""
import os
import shutil
import subprocess
import sys

import pytest

import golden

CASES = golden.CASES_DIR


def run(*args, cwd=None):
    return subprocess.run([sys.executable, golden.SCRIPT, *args], capture_output=True, text=True, cwd=cwd)


def test_single_file_ok_line(tmp_path):
    out = tmp_path / "c01.svg"
    r = run(os.path.join(CASES, "c01-order.svg"), "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert "[OK]" in r.stdout and "masks:1" in r.stdout and out.exists()


def test_invalid_input_fails_without_writing(tmp_path):
    out = tmp_path / "empty.svg"
    r = run(os.path.join(CASES, "r01-empty.svg"), "-o", str(out))
    assert r.returncode == 1
    assert "[FAIL]" in r.stderr and "XMLSyntaxError" in r.stderr
    assert not out.exists()


def test_batch_continues_past_a_bad_file(tmp_path):
    r = run(os.path.join(CASES, "r01-empty.svg"), os.path.join(CASES, "c01-order.svg"),
            "--outdir", str(tmp_path))
    assert r.returncode == 1
    assert (tmp_path / "c01-order.svg").exists()
    assert not (tmp_path / "r01-empty.svg").exists()
    assert "1 file(s) failed" in r.stderr


def test_output_flag_rejects_batches(tmp_path):
    r = run(os.path.join(CASES, "c01-order.svg"), os.path.join(CASES, "c02-wrapper-siblings.svg"),
            "-o", str(tmp_path / "x.svg"))
    assert r.returncode == 2


def test_mode_flags_are_exclusive(tmp_path):
    r = run(os.path.join(CASES, "c01-order.svg"), "--in-place", "--outdir", str(tmp_path))
    assert r.returncode == 2


def test_in_place_is_idempotent(tmp_path):
    src = tmp_path / "c01.svg"
    shutil.copy(os.path.join(CASES, "c01-order.svg"), src)
    assert run(str(src), "--in-place").returncode == 0
    first = src.read_bytes()
    assert run(str(src), "--in-place").returncode == 0
    assert src.read_bytes() == first


def test_missing_input_is_skipped_not_fatal(tmp_path):
    r = run(str(tmp_path / "nope.svg"), os.path.join(CASES, "c01-order.svg"), "--outdir", str(tmp_path))
    assert r.returncode == 0 and "[SKIP]" in r.stderr


def test_no_outline_stroke_keeps_strokes(tmp_path):
    out = tmp_path / "s.svg"
    r = run(os.path.join(CASES, "x01-inherited-stroke.svg"), "-o", str(out), "--no-outline-stroke")
    assert r.returncode == 0 and "strokes:0" in r.stdout
    assert 'stroke="' in out.read_text()


@pytest.fixture
def sibling_dir(tmp_path):
    a = tmp_path / "a.svg"
    b = tmp_path / "b.svg"
    a.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>')
    b.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M0 0h20v20H0z"/></svg>')
    return a, b


def test_sibling_viewbox_mismatch_is_reported(sibling_dir):
    a, b = sibling_dir
    r = run(str(a), str(b), "--in-place")
    assert r.returncode == 0  # a warning, not a failure
    assert "Sibling viewBox mismatch" in r.stderr and "24×24" in r.stderr and "20×20" in r.stderr


def test_sibling_check_can_be_disabled(sibling_dir):
    a, b = sibling_dir
    r = run(str(a), str(b), "--in-place", "--no-sibling-check")
    assert "Sibling viewBox mismatch" not in r.stderr


def test_sibling_check_groups_by_imageset_parent(tmp_path):
    d = tmp_path / "Tab"
    (d / "home.imageset").mkdir(parents=True)
    (d / "search.imageset").mkdir()
    a = d / "home.imageset" / "home.svg"
    b = d / "search.imageset" / "search.svg"
    a.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>')
    b.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><path d="M0 0h28v28H0z"/></svg>')
    r = run(str(a), str(b), "--in-place")
    assert "Sibling viewBox mismatch" in r.stderr and str(d) in r.stderr
