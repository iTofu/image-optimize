"""Unit tests for the pure helpers in svg_optimize.py: the decisions that gate whether a mask
or stroke is touched at all. Each one is a place a reviewer once found a hole."""
import os

import pytest
from lxml import etree

import golden

so = golden.optimizer()
SVG = "http://www.w3.org/2000/svg"


def el(xml: str):
    """Parse a fragment inside an <svg> and return its first child element."""
    root = etree.fromstring(f'<svg xmlns="{SVG}">{xml}</svg>'.encode())
    return root[0]


# --- paint ---------------------------------------------------------------------------------

@pytest.mark.parametrize("paint, alpha", [
    ("#000", 1.0), ("red", 1.0), ("url(#g)", 1.0), ("none", 1.0),
    ("rgba(0,0,0,0.5)", 0.5), ("rgba(0, 0, 0, 50%)", 0.5), ("hsla(0,0%,0%,.25)", 0.25),
    ("#0008", 0x88 / 255), ("#00000080", 0x80 / 255), ("#000000ff", 1.0),
])
def test_paint_alpha(paint, alpha):
    assert so._paint_alpha(paint) == pytest.approx(alpha)


def test_bbox_gradient_is_refused():
    root = etree.fromstring(
        f'<svg xmlns="{SVG}"><linearGradient id="g"/><linearGradient id="u" gradientUnits="userSpaceOnUse"/>'
        f'<linearGradient id="h" href="#g"/><linearGradient id="hu" xlink:href="#u" xmlns:xlink="http://www.w3.org/1999/xlink"/>'
        f'<pattern id="p" patternUnits="userSpaceOnUse"/><path fill="url(#g)"/></svg>'.encode())
    path = root[-1]
    with pytest.raises(so.UnsupportedSVG):
        so._check_paint(path, "url(#g)")  # gradientUnits defaults to objectBoundingBox
    so._check_paint(path, "url(#u)")  # userSpaceOnUse is fine
    with pytest.raises(so.UnsupportedSVG):
        so._check_paint(path, "url(#h)")  # inherits objectBoundingBox through href
    so._check_paint(path, "url(#hu)")  # inherits userSpaceOnUse through xlink:href
    with pytest.raises(so.UnsupportedSVG):
        so._check_paint(path, "url(#p)")  # patterns are never flattened
    so._check_paint(path, "#fff")  # plain colours never raise
    so._check_paint(path, None)


# --- numbers ------------------------------------------------------------------------------

@pytest.mark.parametrize("v, out", [("1", 1.0), ("1.5px", 1.5), (" .5 ", 0.5), ("-2e1", -20.0), (None, 7.0)])
def test_num(v, out):
    assert so._num(v, 7.0) == out


@pytest.mark.parametrize("v", ["1em", "50%", "auto", "1 2"])
def test_num_refuses_units_it_cannot_resolve(v):
    with pytest.raises(so.UnsupportedSVG):
        so._num(v)


# --- paint-order --------------------------------------------------------------------------

@pytest.mark.parametrize("order, stroke_first", [
    (None, False), ("normal", False), ("fill", False), ("fill stroke", False),
    ("stroke", True), ("stroke fill", True), ("markers stroke", True), ("markers", False),
])
def test_stroke_before_fill(order, stroke_first):
    attr = f' paint-order="{order}"' if order else ""
    assert so._stroke_before_fill(el(f"<path{attr}/>")) is stroke_first


def test_stroke_before_fill_inherits_from_group():
    g = el('<g paint-order="stroke"><path/></g>')
    assert so._stroke_before_fill(g[0]) is True


# --- markers --------------------------------------------------------------------------------

def test_has_markers_reads_attribute_style_and_inheritance():
    assert not so._has_markers(el("<path/>"))
    assert so._has_markers(el('<path marker-end="url(#m)"/>'))
    assert so._has_markers(el('<path style="marker-start: url(#m)"/>'))
    assert so._has_markers(el('<g marker="url(#m)"><path/></g>')[0])
    assert not so._has_markers(el('<g marker="url(#m)"><path marker="none"/></g>')[0])


# --- bounding-box laid-out effects -----------------------------------------------------------

@pytest.mark.parametrize("xml, bound", [
    ("<clipPath/>", False),
    ('<clipPath clipPathUnits="objectBoundingBox"/>', True),
    ("<mask/>", True),  # maskUnits defaults to objectBoundingBox
    ('<mask maskUnits="userSpaceOnUse"/>', False),
    ('<mask maskUnits="userSpaceOnUse" maskContentUnits="objectBoundingBox"/>', True),
    ("<filter/>", True),  # filterUnits defaults to objectBoundingBox
    ('<filter filterUnits="userSpaceOnUse"/>', False),
    ('<filter filterUnits="userSpaceOnUse" primitiveUnits="objectBoundingBox"/>', True),
    ("<g/>", False),
])
def test_bbox_bound(xml, bound):
    assert so._bbox_bound(el(xml)) is bound


def test_bbox_bound_ancestor_walks_up_and_ignores_userspace():
    root = etree.fromstring(
        f'<svg xmlns="{SVG}"><clipPath id="u" clipPathUnits="userSpaceOnUse"/><filter id="f" filterUnits="userSpaceOnUse"/>'
        f'<g clip-path="url(#u)"><g filter="url(#f)"><path id="ok"/></g></g>'
        f'<mask id="m" maskUnits="userSpaceOnUse"/><g mask="url(#m)"><path id="bad"/></g></svg>'.encode())
    ok = root.find(f".//{{{SVG}}}path[@id='ok']")
    bad = root.find(f".//{{{SVG}}}path[@id='bad']")
    assert so._bbox_bound_ancestor(ok) is None
    assert so._bbox_bound_ancestor(bad) is None  # userSpaceOnUse region + default content units
    root.find(f".//{{{SVG}}}mask").set("maskContentUnits", "objectBoundingBox")
    assert "mask" in so._bbox_bound_ancestor(bad)


# --- stylesheet guard -----------------------------------------------------------------------

def test_stylesheet_props_collects_property_names_only():
    root = etree.fromstring(
        f'<svg xmlns="{SVG}"><style>/* fill: red in a comment */ .a {{ Stroke: blue; stroke-width:2 }} '
        f'#b:hover {{ marker-end: url(#m) }}</style><style>.c{{opacity:.5}}</style></svg>'.encode())
    assert so._stylesheet_props(root) == {"stroke", "stroke-width", "marker-end", "opacity"}


def test_render_props_cover_markers():
    assert set(so._MARKER_PROPS) <= so._RENDER_PROPS


# --- text-level passes ---------------------------------------------------------------------

def test_resolve_css_vars_takes_fallback_including_nested_parens():
    assert so.resolve_css_vars('fill="var(--c, #123456)"') == 'fill="#123456"'
    assert so.resolve_css_vars('fill="var(--c, rgb(1, 2, 3))"') == 'fill="rgb(1, 2, 3)"'
    assert so.resolve_css_vars('fill="red"') == 'fill="red"'


def test_fix_dimensions_replaces_percent_with_viewbox():
    s = '<svg width="100%" height="100%" viewBox="0 0 24 36" preserveAspectRatio="none">'
    assert so.fix_dimensions(s) == '<svg width="24" height="36" viewBox="0 0 24 36" >'
    assert so.fix_dimensions('<svg width="100%">') == '<svg width="100%">'  # no viewBox: leave it


def test_normalize_use_href_rewrites_svg2_href_only():
    out = so.normalize_use_href(f'<svg xmlns="{SVG}"><use href="#a"/></svg>')
    use = etree.fromstring(out.encode())[0]
    assert use.get(so._XLINK_HREF) == "#a" and use.get("href") is None
    same = f'<svg xmlns="{SVG}" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="#a"/></svg>'
    assert so.normalize_use_href(same) is same


# --- sibling grouping ------------------------------------------------------------------------

def test_sibling_group_key_steps_out_of_imageset():
    flat = os.path.abspath("icons/a.svg")
    assert so._sibling_group_key(flat) == os.path.dirname(flat)
    nested = os.path.abspath("Assets.xcassets/Tab/home.imageset/home.svg")
    assert so._sibling_group_key(nested) == os.path.abspath("Assets.xcassets/Tab")


# --- geometry round trip -----------------------------------------------------------------------

def test_fmt_is_shortest_4_decimal_form():
    assert so._fmt(10.0) == "10"
    assert so._fmt(0.123456) == "0.1235"
    assert so._fmt(1.5) == "1.5"
    assert so._fmt(-0.00001) == "0"
    assert so._fmt(-2.25) == "-2.25"


def test_skpath_roundtrip_matches_tidy():
    d = so.skpath_to_d(so.svg_to_skpath("M0 0H10V10H0Z"))
    assert d == "M0,0 L10,0 L10,10 L0,10 Z"
    assert so._tidy(d) == d
    assert so._tidy("M0.123456 1.000000 -0.00001") == "M0.1235 1 0"
