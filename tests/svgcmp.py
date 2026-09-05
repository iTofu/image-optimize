#!/usr/bin/env python3
"""Structural SVG comparison.

Two SVGs are the same when their element trees match: same tags in the same order, same
attributes (attribute order ignored), same text, numbers equal within a small tolerance.
Whitespace, the XML declaration and attribute order are serialisation noise; anything else
is a real change and is reported with the path of the first mismatch.

usage: svgcmp.py A B      files or directories (directories are matched by file name)
exit 1 when anything differs.
"""
import os
import re
import sys

from lxml import etree

NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
TOLERANCE = 1e-3  # path data is written with 4 decimals; this absorbs last-digit float noise


def parse(text_or_bytes):
    if isinstance(text_or_bytes, str):
        text_or_bytes = text_or_bytes.encode()
    return etree.fromstring(text_or_bytes, etree.XMLParser(remove_blank_text=True, remove_comments=True))


def _tag(el) -> str:
    return etree.QName(el.tag).localname if isinstance(el.tag, str) else str(el.tag)


def _values_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    if NUM.sub("#", a) != NUM.sub("#", b):
        return False
    return all(abs(float(x) - float(y)) <= TOLERANCE for x, y in zip(NUM.findall(a), NUM.findall(b)))


def first_diff(a, b, path="") -> str | None:
    """Return a description of the first difference between two element trees, or None."""
    here = f"{path}/{_tag(a)}"
    if _tag(a) != _tag(b):
        return f"{path}: <{_tag(a)}> vs <{_tag(b)}>"
    for k in sorted(set(a.attrib) | set(b.attrib)):
        if k not in a.attrib or k not in b.attrib:
            return f"{here}: attribute {etree.QName(k).localname!r} only on {'left' if k in a.attrib else 'right'}"
        if not _values_equal(a.attrib[k], b.attrib[k]):
            return f"{here}: {etree.QName(k).localname}={a.attrib[k]!r} vs {b.attrib[k]!r}"
    if (a.text or "").strip() != (b.text or "").strip():
        return f"{here}: text {(a.text or '').strip()!r} vs {(b.text or '').strip()!r}"
    if len(a) != len(b):
        return f"{here}: {len(a)} children vs {len(b)}"
    for i, (ca, cb) in enumerate(zip(a, b)):
        d = first_diff(ca, cb, f"{here}[{i}]")
        if d:
            return d
    return None


def _compare_files(a, b) -> str | None:
    return first_diff(parse(open(a, "rb").read()), parse(open(b, "rb").read()))


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    a, b = argv[1], argv[2]
    if os.path.isdir(a) and os.path.isdir(b):
        names = sorted({n for d in (a, b) for n in os.listdir(d) if n.endswith(".svg")})
        pairs = [(os.path.join(a, n), os.path.join(b, n)) for n in names]
    else:
        pairs = [(a, b)]
    bad = 0
    for x, y in pairs:
        missing = [p for p in (x, y) if not os.path.exists(p)]
        if missing:
            print(f"MISSING {missing[0]}")
            bad += 1
            continue
        d = _compare_files(x, y)
        if d:
            print(f"DIFF {os.path.basename(x)}: {d}")
            bad += 1
    print(f"{len(pairs) - bad} same, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
