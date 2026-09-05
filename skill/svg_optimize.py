#!/usr/bin/env python3
"""
SVG optimizer for device compatibility.

Transforms SVGs into a clean, device-compatible format by:
- Flattening alpha masks into pure geometry paths
- Converting strokes to filled paths (outline stroke)
- Resolving CSS var() to actual color values
- Fixing percentage-based dimensions
- Converting basic shapes to paths
- Expanding <use> references
- Removing invisible elements

Dependencies: pip3 install picosvg

Usage:
    python3 svg_optimize.py input.svg -o output.svg
    python3 svg_optimize.py *.svg --outdir cleaned/
    python3 svg_optimize.py *.svg --outdir cleaned/ --no-outline-stroke
    python3 svg_optimize.py *.svg --in-place      # batch in-place, keeps sibling check
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

try:
    from lxml import etree
    from pathops import Path as SkPath, op, PathOp, PathVerb
    from picosvg.svg_pathops import skia_path
    from picosvg.svg_types import SVGPath
    from picosvg.svg_transform import Affine2D
    from picosvg.svg import SVG as PicoSVG
except ImportError:
    print("ERROR: picosvg not installed. Run: pip3 install picosvg", file=sys.stderr)
    sys.exit(1)

SVG_NS = "http://www.w3.org/2000/svg"
STROKE_TAGS = ["path", "circle", "ellipse", "rect", "line", "polygon", "polyline"]
CAP_MAP = {"butt": 0, "round": 1, "square": 2}
JOIN_MAP = {"miter": 0, "round": 1, "bevel": 2}


# ---------- Path conversion utilities ----------

_SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline"}
_DEF_TAGS = {"defs", "title", "desc", "metadata", "mask", "clipPath", "linearGradient",
             "radialGradient", "pattern", "style", "symbol", "marker", "filter"}
_GEOMETRY_ATTRS = ("d", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r",
                   "x1", "y1", "x2", "y2", "points")
_STROKE_ATTRS = ("stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
                 "stroke-miterlimit", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset")
_WHITE = {"white", "#fff", "#ffffff", "rgb(255,255,255)", "rgb(255, 255, 255)"}
_BLACK = {"black", "#000", "#000000", "rgb(0,0,0)", "rgb(0, 0, 0)"}
_NO_PAINT = {"none", "transparent"}
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_URL_REF = re.compile(r"""url\(\s*['"]?#([^'")\s]+)['"]?\s*\)""")
_NUMBER = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(px)?\s*$")


class UnsupportedSVG(Exception):
    """A construct this optimizer refuses to guess about. The element is left untouched."""


def _localname(el) -> str:
    return etree.QName(el.tag).localname if isinstance(el.tag, str) else ""


def _style(el) -> dict:
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _set_style(el, style: dict) -> None:
    if style:
        el.set("style", ";".join(f"{k}:{v}" for k, v in style.items()))
    elif "style" in el.attrib:
        del el.attrib["style"]


def _attr(el, name: str) -> Optional[str]:
    """Presentation attribute; inline style wins over the attribute (CSS cascade)."""
    v = _style(el).get(name)
    return v if v is not None else el.get(name)


def _resolved(el, name: str, default: Optional[str] = None) -> Optional[str]:
    """Inherited presentation attribute: nearest ancestor-or-self value."""
    node = el
    while node is not None and isinstance(node.tag, str):
        v = _attr(node, name)
        if v is not None and v != "inherit":
            return v
        node = node.getparent()
    return default


def _num(v, default: float = 0.0) -> float:
    if v is None:
        return default
    m = _NUMBER.match(str(v))
    if not m:
        raise UnsupportedSVG(f"non-numeric length {v!r}")
    return float(m.group(1))


def _affine(el) -> Affine2D:
    t = _attr(el, "transform")
    return Affine2D.fromstring(t) if t else Affine2D.identity()


def _apply(sk: SkPath, aff: Affine2D) -> SkPath:
    if aff == Affine2D.identity():
        return sk
    return sk.transform(aff.a, aff.b, aff.c, aff.d, aff.e, aff.f)


def _ref_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    m = _URL_REF.search(value)
    return m.group(1) if m else None


def _strip_attrs(el, names) -> None:
    style = _style(el)
    for n in names:
        if n in el.attrib:
            del el.attrib[n]
        style.pop(n, None)
    _set_style(el, style)


_RGBA = re.compile(r"(?:rgba|hsla)\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*([\d.]+%?)\s*\)")


def _paint_alpha(paint: str) -> float:
    """Alpha channel of a colour literal (rgba / hsla / #rgba / #rrggbbaa); 1.0 when opaque or unknown."""
    m = _RGBA.match(paint)
    if m:
        a = m.group(1)
        return float(a[:-1]) / 100 if a.endswith("%") else float(a)
    if paint.startswith("#") and len(paint) in (5, 9):
        h = paint[-1] * 2 if len(paint) == 5 else paint[-2:]
        return int(h, 16) / 255
    return 1.0


_MARKER_PROPS = ("marker", "marker-start", "marker-mid", "marker-end")
_RENDER_PROPS = {"fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
                 "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "stroke-dasharray",
                 "stroke-dashoffset", "opacity", "mask", "mask-type", "clip-path", "filter", "visibility",
                 "display", "vector-effect", "paint-order", "transform", *_MARKER_PROPS}
_CSS_PROP = re.compile(r"([a-zA-Z-]+)\s*:")


def _stylesheet_props(root) -> set:
    """Property names set anywhere in <style> sheets (rules are not applied; this only tells whether
    they could affect what the passes resolve)."""
    props = set()
    for style in root.iter(f"{{{SVG_NS}}}style"):
        css = re.sub(r"/\*.*?\*/", "", style.text or "", flags=re.S)
        for block in re.findall(r"\{([^}]*)\}", css):
            props.update(p.lower() for p in _CSS_PROP.findall(block))
    return props


def _isolate_generated(e, parent) -> None:
    """Generated geometry is nonzero-wound: cut off an inherited evenodd rule."""
    if _resolved(parent, "fill-rule", "nonzero") == "evenodd":
        e.set("fill-rule", "nonzero")


def _bbox_bound(target) -> bool:
    """Whether a clipPath / mask / filter definition is laid out against the referencing element's
    bounding box (region or content units); the default units of mask and filter regions are."""
    tag = _localname(target)
    if tag == "clipPath":
        return target.get("clipPathUnits") == "objectBoundingBox"
    if tag == "mask":
        return target.get("maskUnits") != "userSpaceOnUse" or target.get("maskContentUnits") == "objectBoundingBox"
    if tag == "filter":
        return target.get("filterUnits") != "userSpaceOnUse" or target.get("primitiveUnits") == "objectBoundingBox"
    return False


def _bbox_bound_ancestor(el) -> Optional[str]:
    """Describe an ancestor effect laid out against that ancestor's bounding box: changing a
    descendant's geometry would move it. None when there is none."""
    root = el.getroottree().getroot()
    for a in el.iterancestors():
        if not isinstance(a.tag, str):
            continue
        for attr in ("clip-path", "mask", "filter"):
            ref = _ref_id(_attr(a, attr))
            if not ref:
                continue
            target = root.find(f".//*[@id='{ref}']")
            if target is not None and _bbox_bound(target):
                return f"<{_localname(a)}> {attr} laid out in objectBoundingBox units"
    return None


def _has_markers(el) -> bool:
    return any(_resolved(el, p, "none") != "none" for p in _MARKER_PROPS)


def _stroke_before_fill(el) -> bool:
    """Effective paint-order puts the stroke under the fill (stages not listed keep their default
    relative order: fill, stroke, markers)."""
    order = _resolved(el, "paint-order", "normal").split()
    if not order or order == ["normal"]:
        return False
    seq = order + [p for p in ("fill", "stroke", "markers") if p not in order]
    return seq.index("stroke") < seq.index("fill")


def _check_paint(el, paint: Optional[str]) -> None:
    """Refuse paint servers laid out against the shape's bounding box. Flattening changes the bbox
    (intersection) or replaces the shape by its outline (stroke), so an objectBoundingBox gradient
    would visibly shift; patterns are not worth guessing about at all."""
    ref = _ref_id(paint)
    if not ref:
        return
    root = el.getroottree().getroot()
    seen = set()
    target = root.find(f".//*[@id='{ref}']")
    while target is not None and ref not in seen:
        seen.add(ref)
        tag = _localname(target)
        if tag == "pattern":
            raise UnsupportedSVG("pattern paint")
        if tag not in ("linearGradient", "radialGradient"):
            return
        units = target.get("gradientUnits")
        if units == "userSpaceOnUse":
            return
        if units is not None:
            break
        href = target.get("href") or target.get(_XLINK_HREF) or ""  # gradientUnits inherits along href
        ref = href[1:] if href.startswith("#") else None
        target = root.find(f".//*[@id='{ref}']") if ref else None
        if target is None:
            break
    if seen:
        raise UnsupportedSVG("objectBoundingBox gradient paint")


def _cmds(d: str):
    """Absolute M/L/C/Q/Z command sequence (arcs and shorthands expanded)."""
    p = SVGPath(d=d)
    p.absolute(inplace=True)
    p.expand_shorthand(inplace=True)
    p.arcs_to_cubics(inplace=True)
    return list(p.as_cmd_seq())


def d_to_skpath(d: str, fill_rule: str = "nonzero") -> SkPath:
    return skia_path(_cmds(d), fill_rule if fill_rule == "evenodd" else "nonzero")


def svg_to_skpath(d: str) -> SkPath:
    """Build skia Path preserving curves (for stroke operations)."""
    path = SkPath()
    for cmd, args in _cmds(d):
        if cmd == "M":
            path.moveTo(args[0], args[1])
        elif cmd == "L":
            path.lineTo(args[0], args[1])
        elif cmd == "C":
            path.cubicTo(args[0], args[1], args[2], args[3], args[4], args[5])
        elif cmd == "Q":
            path.quadTo(args[0], args[1], args[2], args[3])
        elif cmd == "Z":
            path.close()
    return path


def _circle_d(cx, cy, r):
    k = 0.5522847498
    return (f"M{cx},{cy-r} C{cx+r*k},{cy-r} {cx+r},{cy-r*k} {cx+r},{cy} "
            f"C{cx+r},{cy+r*k} {cx+r*k},{cy+r} {cx},{cy+r} "
            f"C{cx-r*k},{cy+r} {cx-r},{cy+r*k} {cx-r},{cy} "
            f"C{cx-r},{cy-r*k} {cx-r*k},{cy-r} {cx},{cy-r} Z")


_FLOAT = re.compile(r"-?\d+\.\d+(?:e-?\d+)?")


def _fmt(v: float) -> str:
    """Shortest 4-decimal form of a coordinate: no trailing zeros, no negative zero."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _tidy(d: str) -> str:
    """Rewrite every float in path data through `_fmt`."""
    return _FLOAT.sub(lambda m: _fmt(float(m.group())), d)


def shape_d(el) -> Optional[str]:
    """Path data equivalent of a basic shape (or the path's own `d`); None if not a shape."""
    tag = _localname(el)
    if tag == "path":
        return el.get("d") or None
    d = _basic_shape_d(el, tag)
    return _tidy(d) if d else None


def _basic_shape_d(el, tag: str) -> Optional[str]:
    if tag == "rect":
        x, y = _num(el.get("x")), _num(el.get("y"))
        w, h = _num(el.get("width")), _num(el.get("height"))
        if w <= 0 or h <= 0:
            return None
        # SVG spec: a missing radius takes the other one's value (in both directions).
        rx = _num(el.get("rx"), _num(el.get("ry")))
        ry = _num(el.get("ry"), rx)
        if rx > 0 or ry > 0:
            rx = min(rx, w / 2)
            ry = min(ry, h / 2)
            return (f"M{x+rx},{y} L{x+w-rx},{y} "
                    f"A{rx},{ry} 0 0 1 {x+w},{y+ry} L{x+w},{y+h-ry} "
                    f"A{rx},{ry} 0 0 1 {x+w-rx},{y+h} L{x+rx},{y+h} "
                    f"A{rx},{ry} 0 0 1 {x},{y+h-ry} L{x},{y+ry} "
                    f"A{rx},{ry} 0 0 1 {x+rx},{y} Z")
        return f"M{x},{y} L{x+w},{y} L{x+w},{y+h} L{x},{y+h} Z"
    elif tag == "circle":
        r = _num(el.get("r"))
        return _circle_d(_num(el.get("cx")), _num(el.get("cy")), r) if r > 0 else None
    elif tag == "ellipse":
        cx, cy = _num(el.get("cx")), _num(el.get("cy"))
        rx, ry = _num(el.get("rx")), _num(el.get("ry"))
        if rx <= 0 or ry <= 0:
            return None
        k = 0.5522847498
        return (f"M{cx},{cy-ry} C{cx+rx*k},{cy-ry} {cx+rx},{cy-ry*k} {cx+rx},{cy} "
                f"C{cx+rx},{cy+ry*k} {cx+rx*k},{cy+ry} {cx},{cy+ry} "
                f"C{cx-rx*k},{cy+ry} {cx-rx},{cy+ry*k} {cx-rx},{cy} "
                f"C{cx-rx},{cy-ry*k} {cx-rx*k},{cy-ry} {cx},{cy-ry} Z")
    elif tag == "line":
        x1, y1 = _num(el.get("x1")), _num(el.get("y1"))
        x2, y2 = _num(el.get("x2")), _num(el.get("y2"))
        return f"M{x1},{y1} L{x2},{y2}"
    elif tag in ("polygon", "polyline"):
        pts = (el.get("points") or "").strip()
        if not pts:
            return None
        coords = pts.replace(",", " ").split()
        pairs = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
        if not pairs:
            return None
        d = f"M{pairs[0][0]},{pairs[0][1]}" + "".join(f" L{x},{y}" for x, y in pairs[1:])
        return d + " Z" if tag == "polygon" else d
    return None


def el_to_skpath(el, fill_rule: str = "nonzero") -> Optional[SkPath]:
    d = shape_d(el)
    return d_to_skpath(d, fill_rule) if d else None


def _stroke_outline(el, d: str) -> Optional[SkPath]:
    """Outline of the element's stroke in its local space; None when it is not stroked."""
    stroke = _resolved(el, "stroke", "none")
    if stroke in _NO_PAINT:
        return None
    if _resolved(el, "stroke-dasharray", "none") not in ("none", "0"):
        raise UnsupportedSVG("stroke-dasharray")
    if _resolved(el, "vector-effect", "none") != "none":
        raise UnsupportedSVG("vector-effect (non-scaling stroke has no fixed local-space outline)")
    if _has_markers(el):
        raise UnsupportedSVG("markers (the outline's vertices are not the original path's)")
    _check_paint(el, stroke)
    width = _num(_resolved(el, "stroke-width", "1"))
    if width <= 0:
        return None
    sk = svg_to_skpath(d)
    cap = CAP_MAP.get(_resolved(el, "stroke-linecap", "butt"), 0)
    join = JOIN_MAP.get(_resolved(el, "stroke-linejoin", "miter"), 0)
    miter = _num(_resolved(el, "stroke-miterlimit", "4"))
    sk.stroke(width, cap, join, miter)
    sk.convertConicsToQuads()  # boolean ops reject the conic segments round caps / joins produce
    return sk


def _is_empty_d(d: str) -> bool:
    return not d or d.strip() in ("", "Z")


_VERB_LETTER = {PathVerb.MOVE: "M", PathVerb.LINE: "L", PathVerb.CUBIC: "C", PathVerb.QUAD: "Q"}


def skpath_to_d(path: SkPath) -> str:
    """Path data for a skia path, with coordinates in the same `_fmt` form `_tidy` produces."""
    path.convertConicsToQuads()
    parts = []
    for verb, pts in path:
        if verb == PathVerb.CLOSE:
            parts.append("Z")
        elif verb in _VERB_LETTER:
            parts.append(_VERB_LETTER[verb] + " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts))
    return " ".join(parts)


# ---------- SVG transform passes ----------

def resolve_css_vars(svg_str: str) -> str:
    """Replace var(--name, fallback) with the fallback value.
    Handles nested parens like var(--color, rgb(0,0,0))."""
    def _replace_var(m):
        inner = m.group(0)[4:-1]  # strip 'var(' and trailing ')'
        comma_idx = inner.index(",")
        return inner[comma_idx + 1:].strip()
    return re.sub(r'var\(--[^,]+,\s*(?:[^()]*|\([^)]*\))+\)', _replace_var, svg_str)


def fix_dimensions(svg_str: str) -> str:
    svg_str = svg_str.replace('preserveAspectRatio="none"', '')
    svg_str = svg_str.replace('overflow="visible"', '')
    svg_str = svg_str.replace('style="display: block;"', '')
    vb = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', svg_str)
    if vb and 'width="100%"' in svg_str:
        svg_str = svg_str.replace('width="100%"', f'width="{vb.group(1)}"')
        svg_str = svg_str.replace('height="100%"', f'height="{vb.group(2)}"')
    return svg_str


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}", file=sys.stderr)


# ---------- Mask flattening ----------

def _mask_geometry(mask_el) -> SkPath:
    """Geometry of the visible (opaque) region of a mask, in the user space of the element
    referencing it. Only hard-edged masks are representable as geometry: anything with partial
    alpha (opacity, non black/white luminance) or non-shape content raises UnsupportedSVG."""
    if mask_el.get("maskContentUnits") == "objectBoundingBox":
        raise UnsupportedSVG("maskContentUnits=objectBoundingBox")
    alpha = (_attr(mask_el, "mask-type") or "luminance").strip() == "alpha"
    result = SkPath()

    def combine(current, sk, paint):
        paint = (paint or "black").strip().lower()
        if paint in ("none", "transparent"):
            return current
        if _ref_id(paint):
            raise UnsupportedSVG("gradient / pattern paint inside <mask>")
        if _paint_alpha(paint) < 1.0:
            raise UnsupportedSVG(f"translucent paint {paint!r} inside <mask> (soft mask)")
        if alpha:
            return op(current, sk, PathOp.UNION, fix_winding=True)
        if paint in _WHITE:
            return op(current, sk, PathOp.UNION, fix_winding=True)
        if paint in _BLACK:
            return op(current, sk, PathOp.DIFFERENCE, fix_winding=True)
        raise UnsupportedSVG(f"luminance mask with non black/white paint {paint!r} (soft mask)")

    def visit(node, aff):
        nonlocal result
        for child in node:
            tag = _localname(child)
            if not tag or tag in _DEF_TAGS:
                continue
            # `opacity` is per element; fill/stroke-opacity inherit (also from <mask> itself).
            if _num(_attr(child, "opacity"), 1.0) < 1.0:
                raise UnsupportedSVG("opacity inside <mask> (soft mask)")
            for a in ("fill-opacity", "stroke-opacity"):
                if _num(_resolved(child, a), 1.0) < 1.0:
                    raise UnsupportedSVG(f"{a} inside <mask> (soft mask)")
            if _attr(child, "display") == "none":
                continue
            # visibility is inherited but overridable by descendants: decide it per leaf, not per group.
            if tag != "g" and _resolved(child, "visibility", "visible") == "hidden":
                continue
            if _attr(child, "mask") or _attr(child, "clip-path") or _attr(child, "filter"):
                raise UnsupportedSVG("nested mask / clip-path / filter inside <mask>")
            child_aff = Affine2D.compose_ltr((_affine(child), aff))
            if tag == "g":
                visit(child, child_aff)
                continue
            if tag not in _SHAPE_TAGS:
                raise UnsupportedSVG(f"<{tag}> inside <mask>")
            d = shape_d(child)
            if not d:
                continue
            if _has_markers(child):
                raise UnsupportedSVG("markers inside <mask>")
            fill = _resolved(child, "fill", "black")
            layers = []  # painted in order, so later layers win in the luminance combine
            if fill not in ("none", "transparent"):
                layers.append((_apply(d_to_skpath(d, _resolved(child, "fill-rule", "nonzero")), child_aff), fill))
            outline = _stroke_outline(child, d)
            if outline is not None:
                layers.append((_apply(outline, child_aff), _resolved(child, "stroke")))
            if len(layers) == 2 and _stroke_before_fill(child):
                layers.reverse()
            for sk, paint in layers:
                result = combine(result, sk, paint)

    visit(mask_el, Affine2D.identity())

    # Mask region (x/y/width/height) clips the mask content. Only the explicit userSpaceOnUse form
    # is representable; the objectBoundingBox default (-10% .. 120% of the bbox) is a superset of
    # the content for well-formed exports and is left out.
    if mask_el.get("maskUnits") == "userSpaceOnUse" and all(mask_el.get(k) for k in ("x", "y", "width", "height")):
        x, y = _num(mask_el.get("x")), _num(mask_el.get("y"))
        w, h = _num(mask_el.get("width")), _num(mask_el.get("height"))
        region = d_to_skpath(f"M{x},{y} L{x+w},{y} L{x+w},{y+h} L{x},{y+h} Z")
        result = op(region, result, PathOp.INTERSECTION, fix_winding=True)
    return result


def _plan_leaves(el, mask_sk: SkPath):
    """Every paintable shape under `el` (inclusive) paired with the mask expressed in that shape's
    own local coordinate space. Raises UnsupportedSVG before anything is mutated."""
    plan = []

    def visit(node, to_el: Affine2D):
        # to_el maps node-local coordinates into the masked element's user space.
        tag = _localname(node)
        if not tag or tag in _DEF_TAGS:
            return
        # Inner masks are flattened before outer ones, so a mask attribute still present here is one
        # that could not be flattened; stripping it along with the leaf would silently drop it.
        if node is not el and _attr(node, "mask"):
            raise UnsupportedSVG(f"<{tag}> with an unflattened mask inside masked content")
        # A clip-path / filter on the host or below would apply to the cut geometry instead of the
        # original (bbox-relative clips shrink, filters change order with the mask).
        if _attr(node, "clip-path") or _attr(node, "filter"):
            raise UnsupportedSVG(f"<{tag}> with clip-path / filter in masked content")
        if tag == "g":
            for child in node:
                visit(child, Affine2D.compose_ltr((_affine(child), to_el)))
            return
        if tag not in _SHAPE_TAGS:
            raise UnsupportedSVG(f"<{tag}> inside masked content")
        if to_el.is_degenerate():
            plan.append((node, None))  # collapsed to nothing: invisible
            return
        d = shape_d(node)
        if not d:
            plan.append((node, None))
            return
        # Validate everything that can be refused before any leaf is mutated.
        if _has_markers(node):
            raise UnsupportedSVG("markers (the cut geometry's vertices are not the original path's)")
        _check_paint(node, _resolved(node, "fill", "black"))
        _stroke_outline(node, d)
        _num(_attr(node, "opacity"), 1.0)
        plan.append((node, _apply(mask_sk, to_el.inverse())))

    bound = _bbox_bound_ancestor(el)
    if bound:  # the intersection shrinks the ancestor's bounding box, which that effect is laid out against
        raise UnsupportedSVG(f"ancestor {bound}")
    visit(el, Affine2D.identity())
    return plan


def _stroke_path(leaf, sk: SkPath):
    """A filled <path> carrying `leaf`'s stroke outline and paint; None when the outline is empty."""
    d = skpath_to_d(sk)
    if _is_empty_d(d):
        return None
    e = etree.Element(f"{{{SVG_NS}}}path", attrib=dict(leaf.attrib))
    # `fill` must go from the inline style too, or `style="fill:none"` outranks the attribute set below.
    _strip_attrs(e, _GEOMETRY_ATTRS + _STROKE_ATTRS + ("fill", "fill-rule", "fill-opacity"))
    e.set("d", d)
    e.set("fill", _resolved(leaf, "stroke"))
    stroke_opacity = _resolved(leaf, "stroke-opacity")
    if stroke_opacity is not None or _resolved(leaf.getparent(), "fill-opacity") is not None:
        e.set("fill-opacity", stroke_opacity or "1")  # never inherit the fill's opacity
    _isolate_generated(e, leaf.getparent())
    return e


def _replace(leaf, fill_e, stroke_e) -> int:
    """Put `fill_e` and `stroke_e` (either may be None) in `leaf`'s slot, so z-order, ancestor
    transforms and inherited styling are untouched. Returns the number of paths placed.

    The emitted paths must not be stroked again: when an ancestor supplies a stroke they cut the
    inheritance off explicitly. A translucent shape that keeps both its fill and its stroke outline
    is wrapped in <g opacity> so the pair still composites as a single layer, as the original did."""
    parent = leaf.getparent()
    idx = parent.index(leaf)
    inherited_stroke = _resolved(parent, "stroke", "none") not in _NO_PAINT
    opacity = _attr(leaf, "opacity")
    stroke_first = _stroke_before_fill(leaf)
    parent.remove(leaf)  # first: fill_e may be the leaf itself
    emitted = [e for e in ((stroke_e, fill_e) if stroke_first else (fill_e, stroke_e)) if e is not None]
    count = len(emitted)
    for e in emitted:
        if inherited_stroke:
            e.set("stroke", "none")
    if count == 2 and stroke_e.get("id"):  # the stroke path only needs its own id next to a fill path
        stroke_e.set("id", stroke_e.get("id") + "-stroke")
    if count == 2 and _num(opacity, 1.0) < 1.0:
        g = etree.Element(f"{{{SVG_NS}}}g")
        g.set("opacity", opacity)
        for e in emitted:
            _strip_attrs(e, ("opacity",))
            g.append(e)
        emitted = [g]
    for i, e in enumerate(emitted):
        parent.insert(idx + i, e)
    return count


def _flatten_leaf(leaf, mask_local: Optional[SkPath]) -> int:
    """Replace `leaf` by its fill (and stroke outline) intersected with the mask. Returns the number
    of paths emitted."""
    fill_e = stroke_e = None
    hidden = _attr(leaf, "display") == "none" or _resolved(leaf, "visibility", "visible") == "hidden"
    if mask_local is not None and not hidden:
        d = shape_d(leaf)
        if _resolved(leaf, "fill", "black") not in _NO_PAINT:
            sk = op(mask_local, d_to_skpath(d, _resolved(leaf, "fill-rule", "nonzero")),
                    PathOp.INTERSECTION, fix_winding=True)
            fd = skpath_to_d(sk)
            if not _is_empty_d(fd):
                fill_e = etree.Element(f"{{{SVG_NS}}}path", attrib=dict(leaf.attrib))
                _strip_attrs(fill_e, _GEOMETRY_ATTRS + _STROKE_ATTRS + ("fill-rule", "mask"))
                fill_e.set("d", fd)
                _isolate_generated(fill_e, leaf.getparent())
        outline = _stroke_outline(leaf, d)
        if outline is not None:
            stroke_e = _stroke_path(leaf, op(mask_local, outline, PathOp.INTERSECTION, fix_winding=True))
            if stroke_e is not None:
                _strip_attrs(stroke_e, ("mask",))  # the mask is now baked into the geometry
    return _replace(leaf, fill_e, stroke_e)


def flatten_masks(root) -> dict:
    """Flatten alpha / hard luminance masks into pure geometry. Returns stats.

    Each masked element is rewritten in place: every shape underneath it becomes the intersection
    of its own geometry (fill and stroke outline separately) with the mask, expressed in the shape's
    local coordinates. Nothing moves in the tree, so paint order, ancestor transforms, group
    opacity and inherited styling all survive. Elements whose mask or content cannot be expressed
    as hard geometry are left untouched (with their <mask> definition) and reported."""
    mask_els = {m.get("id"): m for m in root.iter(f"{{{SVG_NS}}}mask") if m.get("id")}
    stats = {"masks": 0, "paths": 0, "skipped": 0}
    if not mask_els:
        return stats

    geometries = {}
    for mid, mask_el in mask_els.items():
        try:
            geometries[mid] = _mask_geometry(mask_el)
        except UnsupportedSVG as e:
            geometries[mid] = None
            _warn(f"mask #{mid} left as-is: {e}")

    def depth(el):
        n = 0
        while el.getparent() is not None:
            el = el.getparent()
            n += 1
        return n

    masked = [el for el in root.iter() if isinstance(el.tag, str) and _ref_id(_attr(el, "mask"))]
    # Innermost first: a masked shape inside a masked group is resolved before the group treats it
    # as an ordinary leaf, so both masks apply.
    masked.sort(key=depth, reverse=True)

    for el in masked:
        mid = _ref_id(_attr(el, "mask"))
        if mid not in mask_els:
            _warn(f"<{_localname(el)}> references missing mask #{mid}; left as-is")
            stats["skipped"] += 1
            continue
        mask_sk = geometries[mid]
        if mask_sk is None:
            stats["skipped"] += 1
            continue
        try:
            plan = _plan_leaves(el, mask_sk)
        except UnsupportedSVG as e:
            _warn(f"<{_localname(el)}> masked by #{mid} left as-is: {e}")
            stats["skipped"] += 1
            continue
        for leaf, mask_local in plan:
            stats["paths"] += _flatten_leaf(leaf, mask_local)
        if el.getparent() is not None:  # a masked shape was replaced by its own paths above
            _strip_attrs(el, ("mask",))
        stats["masks"] += 1

    referenced = {_ref_id(_attr(x, "mask")) for x in root.iter() if isinstance(x.tag, str)}
    for mid, mask_el in mask_els.items():
        if mid not in referenced and mask_el.getparent() is not None:
            mask_el.getparent().remove(mask_el)
    return stats


# ---------- Stroke outlining ----------

def outline_strokes(root) -> int:
    """Convert stroked shapes to filled paths. A shape that is both filled and stroked keeps its fill
    and gains a sibling path for the stroke outline right after it (same paint order as the
    stroke-over-fill rendering). Returns the number of strokes outlined."""
    count = 0
    for el in [e for e in root.iter() if _localname(e) in _SHAPE_TAGS]:
        if any(_localname(a) in _DEF_TAGS for a in el.iterancestors()):
            continue
        if _resolved(el, "stroke", "none") in _NO_PAINT:
            continue
        held = [a for a in ("mask", "clip-path", "filter") if _attr(el, a)]
        if held:  # splitting into fill + outline paths cannot keep these applying to the shape as a whole
            _warn(f"stroke on <{_localname(el)}> left as-is: carries {', '.join(held)}")
            continue
        bound = _bbox_bound_ancestor(el)
        if bound:  # the outline grows the ancestor's bounding box, which that effect is laid out against
            _warn(f"stroke on <{_localname(el)}> left as-is: ancestor {bound}")
            continue
        d = shape_d(el)
        if not d:
            continue
        try:
            outline = _stroke_outline(el, d)
            _num(_attr(el, "opacity"), 1.0)
        except UnsupportedSVG as e:
            _warn(f"stroke on <{_localname(el)}> left as-is: {e}")
            continue
        if outline is None:  # stroke-width 0: paints nothing, but must keep cutting off an inherited stroke
            _strip_attrs(el, _STROKE_ATTRS)
            if _resolved(el.getparent(), "stroke", "none") not in _NO_PAINT:
                el.set("stroke", "none")
            continue
        stroke_e = _stroke_path(el, outline)
        if stroke_e is None:
            continue
        fill_e = None
        if _resolved(el, "fill", "black") not in _NO_PAINT:
            fill_e = el
            _strip_attrs(el, _STROKE_ATTRS)
        _replace(el, fill_e, stroke_e)
        count += 1
    return count


def shapes_to_paths(root) -> int:
    """Rewrite rect / circle / ellipse / line / polygon / polyline as <path>, keeping every other
    attribute. (picosvg's own pass rebuilds the element from a fixed field list and silently drops
    anything else, e.g. a `mask` that could not be flattened.) Returns the number converted."""
    count = 0
    for el in [e for e in root.iter() if _localname(e) in _SHAPE_TAGS - {"path"}]:
        try:
            d = shape_d(el)
        except UnsupportedSVG as e:
            _warn(f"<{_localname(el)}> left as-is: {e}")
            continue
        if d is None:  # zero-sized: paints nothing
            el.getparent().remove(el)
            continue
        el.tag = f"{{{SVG_NS}}}path"
        _strip_attrs(el, _GEOMETRY_ATTRS)
        el.set("d", d)
        count += 1
    return count


def normalize_use_href(svg_str: str) -> str:
    """Rewrite SVG 2 `href` on <use> as `xlink:href`: picosvg only resolves the latter."""
    if "<use" not in svg_str or "href=" not in svg_str:
        return svg_str
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.fromstring(svg_str.encode(), parser)
    changed = False
    for use in root.iter(f"{{{SVG_NS}}}use"):
        if use.get("href") and not use.get(_XLINK_HREF):
            use.set(_XLINK_HREF, use.get("href"))
            del use.attrib["href"]
            changed = True
    if not changed:
        return svg_str
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode()


def picosvg_pass(svg_str: str, name: str) -> str:
    """Run one picosvg utility pass (`resolve_use` / `remove_unpainted_shapes`) on the document.
    On failure the document is returned unchanged and the reason reported."""
    try:
        s = PicoSVG.fromstring(svg_str)
        getattr(s, name)(inplace=True)
        return s.tostring()
    except Exception as e:  # noqa: BLE001
        _warn(f"picosvg {name} failed: {e}")
        return svg_str


# ---------- Main pipeline ----------

def optimize_svg(input_path: str, output_path: str, outline: bool = True) -> dict:
    stats = {"file": input_path, "masks": 0, "strokes": 0}

    orig_size = os.path.getsize(input_path)

    with open(input_path) as f:
        svg_str = f.read()

    # Pass 1: text-level transforms
    svg_str = resolve_css_vars(svg_str)
    svg_str = fix_dimensions(svg_str)

    # Pass 2: expand <use> first, so referenced shapes inside masks / masked content are plain
    # geometry by the time the mask pass sees them.
    svg_str = picosvg_pass(normalize_use_href(svg_str), "resolve_use")

    # Pass 3: XML-level transforms (shapes → paths, masks, strokes)
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.fromstring(svg_str.encode(), parser)
    shapes_to_paths(tree)
    styled = _stylesheet_props(tree) & _RENDER_PROPS
    if styled:
        # A <style> sheet can set any of these on any element and this script does not apply CSS
        # selectors, so every paint / effect it resolves could be wrong: leave masks and strokes alone.
        _warn(f"<style> sheet sets {', '.join(sorted(styled))}; masks and strokes left as-is")
        stats["masks_skipped"] = sum(1 for e in tree.iter() if isinstance(e.tag, str) and _ref_id(_attr(e, "mask")))
    else:
        mask_stats = flatten_masks(tree)
        stats["masks"] = mask_stats["masks"]
        stats["masks_skipped"] = mask_stats["skipped"]
        if outline:
            stats["strokes"] = outline_strokes(tree)

    # Serialize back to string
    svg_str = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode()

    # Pass 4: drop shapes that paint nothing (picosvg is stylesheet-blind too: a shape painted only
    # through a CSS class would look unpainted, so this pass is skipped along with the others)
    if not styled:
        svg_str = picosvg_pass(svg_str, "remove_unpainted_shapes")

    # Pass 5: final cleanup of any remaining CSS vars
    svg_str = resolve_css_vars(svg_str)

    Path(output_path).write_text(svg_str, encoding="utf-8")

    new_size = len(svg_str.encode("utf-8"))
    stats["size_before"] = orig_size
    stats["size_after"] = new_size
    return stats


def _parse_viewbox(svg_path: str):
    """Return (w, h) from <svg viewBox="x y w h"> or None if unreadable / absent."""
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        root = etree.parse(svg_path, parser).getroot()
    except Exception:
        return None
    vb = root.get("viewBox")
    if not vb:
        return None
    try:
        parts = [float(v) for v in vb.replace(",", " ").split()]
        if len(parts) != 4:
            return None
        _, _, w, h = parts
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def _sibling_group_key(svg_path: str) -> str:
    """Return the directory used to decide whether two SVGs are siblings.

    iOS asset catalog wraps each image in its own `.imageset` directory, so
    siblings live in the *grandparent* directory (the Group). For flat layouts
    (e.g. `icons/a.svg`, `icons/b.svg`) siblings live in the direct parent.
    The rule: if the immediate parent ends with `.imageset`, step up one level;
    otherwise use the immediate parent as the group key.
    """
    parent = os.path.dirname(os.path.abspath(svg_path))
    if parent.endswith(".imageset"):
        return os.path.dirname(parent)
    return parent


def check_sibling_viewbox_consistency(output_paths):
    """Warn when SVGs in the same sibling group have mismatched viewBox dimensions.

    Rationale: a group of assets that render side-by-side in the UI (e.g. the
    a row of provider icons) MUST share a common viewBox size,
    otherwise a single imageView size constant cannot render them consistently.
    Assets sharing a Group (or plain directory for flat layouts) are treated as
    siblings. See `_sibling_group_key` for the exact grouping rule.

    The function reports inconsistencies to stderr. It does not fail the run —
    the caller decides whether to block on warnings.
    """
    by_dir = {}
    for path in output_paths:
        by_dir.setdefault(_sibling_group_key(path), []).append(path)

    warnings = 0
    for directory, siblings in by_dir.items():
        if len(siblings) < 2:
            continue

        dims = []
        for p in siblings:
            vb = _parse_viewbox(p)
            dims.append((p, vb))

        # Epsilon 0.5pt: tolerate float noise from optimizer, reject meaningful divergence.
        widths = [d[1][0] for d in dims if d[1] is not None]
        heights = [d[1][1] for d in dims if d[1] is not None]
        if not widths:
            continue

        w_span = max(widths) - min(widths)
        h_span = max(heights) - min(heights)
        if w_span <= 0.5 and h_span <= 0.5:
            continue

        warnings += 1
        print(
            f"\n  [WARN] Sibling viewBox mismatch in {directory}:",
            file=sys.stderr,
        )
        for p, vb in dims:
            tag = f"viewBox {vb[0]:g}×{vb[1]:g}" if vb else "viewBox missing / unreadable"
            print(f"    - {os.path.basename(p)}: {tag}", file=sys.stderr)
        print(
            "    Sibling assets in one directory are expected to share viewBox dimensions\n"
            "    so the iOS layer can render them with a single imageView size constant.\n"
            "    Fix: re-export all of them from the same-level Figma frame (typically the\n"
            "    wrapper frame carrying the intended padding), not individual vector layers.",
            file=sys.stderr,
        )
    return warnings


def main():
    parser = argparse.ArgumentParser(description="Optimize SVGs for device compatibility.")
    parser.add_argument("inputs", nargs="+", help="Input SVG file(s)")
    parser.add_argument("-o", "--output", help="Output file (single input only)")
    parser.add_argument("--outdir", help="Output directory for batch processing")
    parser.add_argument("-i", "--in-place", action="store_true",
                        help="Overwrite each input in-place (batch-friendly; preserves sibling viewBox check).")
    parser.add_argument("--suffix", default="_optimized", help="Suffix for output files (default: _optimized)")
    parser.add_argument("--no-outline-stroke", action="store_true", help="Skip stroke-to-fill conversion")
    parser.add_argument("--no-sibling-check", action="store_true",
                        help="Skip sibling viewBox consistency check (see SKILL.md §Sibling check)")
    args = parser.parse_args()

    mode_flags = sum([bool(args.output), bool(args.outdir), bool(args.in_place)])
    if mode_flags > 1:
        print("ERROR: -o/--output, --outdir and --in-place are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if args.output and len(args.inputs) != 1:
        print("ERROR: -o/--output only accepts a single input; use --in-place or --outdir for batches.", file=sys.stderr)
        sys.exit(2)

    outline = not args.no_outline_stroke
    total = {"files": 0, "masks": 0, "strokes": 0, "saved": 0}
    failed = 0
    outputs = []

    for inp in args.inputs:
        if not os.path.isfile(inp):
            print(f"  [SKIP] {inp} (not found)", file=sys.stderr)
            continue

        if args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            out = os.path.join(args.outdir, os.path.basename(inp))
        elif args.in_place:
            out = inp
        elif args.output and len(args.inputs) == 1:
            out = args.output
        else:
            name, ext = os.path.splitext(inp)
            out = f"{name}{args.suffix}{ext}"

        try:
            stats = optimize_svg(inp, out, outline=outline)
        except Exception as e:  # noqa: BLE001
            # One bad file must not take the rest of the batch down; the input is left untouched
            # (nothing is written before the whole pipeline has succeeded).
            print(f"  [FAIL] {inp}: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1
            continue
        saved = stats["size_before"] - stats["size_after"]
        total["files"] += 1
        total["masks"] += stats["masks"]
        total["strokes"] += stats["strokes"]
        total["saved"] += max(saved, 0)
        outputs.append(out)

        skipped = f" masks-left:{stats['masks_skipped']}" if stats.get("masks_skipped") else ""
        print(f"  [OK] {inp} -> {out}  masks:{stats['masks']}{skipped} strokes:{stats['strokes']} "
              f"size:{stats['size_before']}→{stats['size_after']}B")

    if total["files"] > 1:
        print(f"\nTotal: {total['files']} files, {total['masks']} masks flattened, "
              f"{total['strokes']} strokes outlined, {total['saved']}B saved")

    # Sibling viewBox consistency: scoped strictly to the current batch.
    # Intentionally NOT expanded to scan pre-existing SVGs in the same directory —
    # that path produces false positives when unrelated assets happen to live in
    # the same Group (e.g. illustrations next to icon sets). The contract is:
    #   "SVGs you want compared must be in the same batch invocation."
    if not args.no_sibling_check and len(outputs) >= 2:
        check_sibling_viewbox_consistency(outputs)

    if failed:
        print(f"\n{failed} file(s) failed, see [FAIL] lines above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
