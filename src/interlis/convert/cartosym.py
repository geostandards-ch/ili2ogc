"""Convert a resolved INTERLIS `StandardSymbology` sign instance into a pycartosym `Symbolizer`/`StylingRule`.

`FontSymbol` composite geometry is tried as native circles, then squares/triangles, then images, then a general
inline-SVG render. `Fill.hatch`/`Stroke.casing`/`centerLine` are not built: pycartosym's SLD writer rejects them.
"""

import base64
import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from lxml import etree
from pycartosym import get_codec
from pycartosym.codecs.sld import WELL_KNOWN_SHAPES
from pycartosym.models.styles import Metadata, Style, StylingRule, Symbolizer
from pycartosym.models.symbolizers import (
    CircleGraphic,
    ClosedPathGraphic,
    Fill,
    Font,
    ImageGraphic,
    Label,
    Marker,
    RectangleGraphic,
    Resource,
    ShapeOutline,
    Stroke,
    TextAlignment,
    TextGraphic,
    Transform2D,
    UnitPoint,
)
from pycartosym.models.types import Angle, AngleUnit, RGBColor, UnitType, UnitValue
from pycartosym.models.value_expressions import PropertyRef

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.color import lch_to_srgb
from interlis.convert.cql2 import to_cql2
from interlis.convert.jsonfg import read_arc, read_coord
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject
from interlis.xtf.schema import attributes_of, schema_members_of
from interlis.xtf.validate import BOUNDARY_TAGS, extract_reference, find_child, geom_tag

_SE_NS = "http://www.opengis.net/se"
_OGC_NS = "http://www.opengis.net/ogc"

_STROKE_SIGN_CLASS = "PolylineSign"
_FILL_SIGN_CLASS = "SurfaceSign"
_TEXT_SIGN_CLASS = "TextSign"
_MARKER_SIGN_CLASS = "SymbolSign"
_RASTER_SIGN_CLASS = "RasterSign"

# `FontSymbol` geometry is authored at size 1.0 (the model's own
# convention), so shape recognition compares lengths of order 1.
_SHAPE_TOLERANCE = 1e-9

_H_ALIGNMENT = {"Left": "left", "Center": "center", "Right": "right"}
# VALIGNMENT has 5 levels but se:AnchorPoint only 3 vertical positions, so Cap/Base are approximated.
_V_ALIGNMENT = {"Top": "top", "Cap": "top", "Half": "middle", "Base": "bottom", "Bottom": "bottom"}


def color_to_rgb(lum: float, c: float, h: float) -> RGBColor:
    """Convert an INTERLIS `Color` (`L`/`C`/`H`) to `RGBColor` - `T` (transparency) is the caller's job, per site."""
    r, g, b = lch_to_srgb(lum, c, h)
    return RGBColor(r=r, g=g, b=b)


_PX_PER_M: float | None = None
_MM_PER_M: float | None = None
# pycartosym writes a millimetre length as pixels at this pixel size (CartoSym Part 1 default); dashes are whole pixels.
_MM_PER_PIXEL = 0.28


@contextmanager
def paper_as_pixels(dpi: float | None) -> Iterator[None]:
    """Within the block, write symbology lengths (metres on paper) as pixels at `dpi`; `None` keeps metres.

    SLD numbers without a unit are pixels, so raw metres would draw nothing. A dash length is relative to the
    line width and becomes `DLength * width` pixels.
    """
    global _PX_PER_M
    previous, _PX_PER_M = _PX_PER_M, (dpi / 0.0254 if dpi else None)
    try:
        yield
    finally:
        _PX_PER_M = previous


@contextmanager
def paper_as_millimetres() -> Iterator[None]:
    """Write every `StandardSymbology` length as millimetres, inside the block (metres on paper at scale 1.0).

    Millimetres are a screen-space unit every CartoSym renderer supports, unlike `m`, which is a ground unit. The SLD
    writer turns them into pixels at its own pixel size (0.28 mm), so no resolution has to be chosen here.
    """
    global _MM_PER_M
    previous, _MM_PER_M = _MM_PER_M, 1000.0
    try:
        yield
    finally:
        _MM_PER_M = previous


def meters(value: float | None) -> UnitValue | None:
    if value is None:
        return None
    if _PX_PER_M:
        return UnitValue(value=value * _PX_PER_M, unit=UnitType.PIXELS)
    if _MM_PER_M:
        return UnitValue(value=value * _MM_PER_M, unit=UnitType.MILLIMETERS)
    return UnitValue(value=value, unit=UnitType.METERS)


def degrees(value: float | None) -> Angle | None:
    return None if value is None else Angle(value=value, unit=AngleUnit.DEGREES)


def _dash_pixels(dash_pattern: list[float], width: float | None) -> list[int]:
    """Dash lengths as integers: `DLength` x width in pixels under `paper_as_pixels` or `paper_as_millimetres`."""
    if _PX_PER_M and width:
        return [max(1, round(d * width * _PX_PER_M)) for d in dash_pattern]
    if _MM_PER_M and width:
        return [max(1, round(d * width * _MM_PER_M / _MM_PER_PIXEL)) for d in dash_pattern]
    return [round(d) for d in dash_pattern]


def polyline_sign_to_stroke(
    *,
    color: RGBColor | None = None,
    opacity: float | None = None,
    width: float | None = None,
    join: str | None = None,
    cap: str | None = None,
    dash_pattern: list[float] | None = None,
) -> Stroke:
    """Build a `Stroke` from a polyline sign's resolved style and color.

    Lossy: `Stroke.dash_pattern` holds integers, so dash lengths below 1.0 round to 0 and may make the line solid.
    """
    return Stroke(
        color=color,
        opacity=opacity,
        width=meters(width),
        join=join,
        cap=cap,
        dash_pattern=_dash_pixels(dash_pattern, width) if dash_pattern else None,
    )


def surface_sign_to_fill(
    *, fill_color: RGBColor | None = None, opacity: float | None = None, pattern: ImageGraphic | None = None
) -> Fill:
    """Build a `Fill` from `SurfaceSign.FillColor`/`HatchSymb` - `Clip`/`HatchOrg`/`HatchOffset` not resolved."""
    return Fill(color=fill_color, opacity=opacity, pattern=pattern)


def font_symbol_text_to_graphic(*, character: str, font_face: str | None = None) -> TextGraphic:
    """Build a `TextGraphic` for a text `FontSymbol` (`Font.Type = text`) - the character stands in for the glyph."""
    return TextGraphic(text=character, font=Font(face=font_face))


def text_sign_to_label(
    *,
    text: Any = None,
    rotation: Any = None,
    font_face: str | None = None,
    height: float | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    color: RGBColor | None = None,
    opacity: float | None = None,
    h_alignment: str | None = None,
    v_alignment: str | None = None,
) -> Label:
    """Build a `Label` from a `TextSign` - `Striked`/`ClipBox`/`ClipFont` have no pycartosym target, dropped.

    Alignments take INTERLIS's own enum values; `text`/`rotation` accept a literal or a CQL2 `PropertyRef` dict.
    """
    alignment = (
        TextAlignment(
            h_alignment=_H_ALIGNMENT.get(h_alignment) if h_alignment else None,
            v_alignment=_V_ALIGNMENT.get(v_alignment) if v_alignment else None,
        )
        if h_alignment or v_alignment
        else None
    )
    font = Font(face=font_face, size=meters(height), italic=italic, underline=underline, color=color, opacity=opacity)
    transform = Transform2D(orientation=rotation) if rotation is not None else None
    graphic = TextGraphic(text=text, font=font, alignment=alignment, transform=transform)
    return Label(elements=[graphic])


def symbolizer_z_order(priority: float | None) -> float | None:
    """Carry a `PARAMETER Priority` through as `Symbolizer.z_order` - marked "temporary" by pycartosym itself."""
    return priority


class SignLibrary:
    """A parsed `SIGN BASKET` data section, indexed for `Sign := {name}` resolution.

    `by_name` resolves the wire `ili:Name` metaobject identity; `by_tid` resolves a REFERENCE attribute's `ili:ref`
    target within the same basket.
    """

    def __init__(self, basket: XtfBasket) -> None:
        self.by_name: dict[str, XtfObject] = {}
        self.by_tid: dict[str, XtfObject] = {}
        for obj in basket.objects:
            if obj.tid is not None:
                self.by_tid[obj.tid] = obj
            name_nodes = obj.attributes.get("Name")
            if name_nodes and name_nodes[0].text:
                self.by_name[name_nodes[0].text] = obj

    def scalar(self, obj: XtfObject, attr: str) -> str | None:
        nodes = obj.attributes.get(attr)
        return nodes[0].text if nodes else None

    def resolve_ref(self, obj: XtfObject, attr: str) -> XtfObject | None:
        nodes = obj.attributes.get(attr)
        if not nodes:
            return None
        tid = extract_reference(nodes[0])
        return self.by_tid.get(tid) if tid else None

    def resolve_color(self, obj: XtfObject, attr: str) -> tuple[RGBColor | None, float | None]:
        """Resolve a `Color`-typed reference attribute to `(RGBColor, opacity)` - `opacity` is `Color.T`."""
        nodes = obj.attributes.get(attr)
        return self._color_from_ref_node(nodes[0]) if nodes else (None, None)

    def _color_from_ref_node(self, ref_node: RawNode) -> tuple[RGBColor | None, float | None]:
        """Resolve a raw REFERENCE-attribute node (top-level or nested in a structure) to `(RGBColor, opacity)`."""
        tid = extract_reference(ref_node)
        color_obj = self.by_tid.get(tid) if tid else None
        if color_obj is None:
            return None, None
        lum, c, h, t = (self.scalar(color_obj, name) for name in ("L", "C", "H", "T"))
        color = color_to_rgb(float(lum), float(c), float(h)) if lum and c and h else None
        return color, (float(t) if t else None)

    def dash_pattern(self, obj: XtfObject) -> list[float] | None:
        """Read a `LineStyle_Dashed.Dashes` (`LIST OF DashRec`) as a flat `DLength` list, in wire order.

        Each `LIST OF` occurrence is its own top-level `<Dashes>` sibling, not one wrapper holding every `DashRec`.
        """
        occurrences = obj.attributes.get("Dashes") or []
        lengths = []
        for occurrence in occurrences:
            for dash_rec in occurrence.children:
                length_node = next((c for c in dash_rec.children if c.tag == "DLength"), None)
                if length_node is not None and length_node.text is not None:
                    lengths.append(float(length_node.text))
        return lengths or None


def _circle_radius_from_surface(node: RawNode) -> float | None:
    """Read an `SS_Surface` boundary as a circle's own radius, from real wire ARC segments.

    Only a single-boundary surface whose segments after the start COORD are all ARCs carrying an explicit `<R>` is
    handled; otherwise `None`.
    """
    surface = find_child(node, "SURFACE")
    boundary = find_child(surface, "BOUNDARY") if surface is not None else None
    polyline = find_child(boundary, "POLYLINE") if boundary is not None else None
    if polyline is None or len(polyline.children) < 2 or geom_tag(polyline.children[0]) != "COORD":
        return None
    radii: list[float] = []
    for arc in polyline.children[1:]:
        if geom_tag(arc) != "ARC":
            return None
        r_node = find_child(arc, "R")
        if r_node is None or r_node.text is None:
            return None
        radii.append(float(r_node.text))
    return radii[0] if radii and all(abs(r - radii[0]) < 1e-9 for r in radii) else None


def _font_symbol_items(symbol_obj: XtfObject, structure_suffix: str | tuple[str, ...]) -> list[RawNode] | None:
    """Every `FontSymbol.Geometry` item's structure node, or `None` unless all of them are `structure_suffix`.

    The native-shape builders are all-or-nothing: one foreign item aborts the symbol (SVG fallback).
    """
    occurrences = symbol_obj.attributes.get("Geometry") or []
    nodes: list[RawNode] = []
    for occurrence in occurrences:
        if not occurrence.children or not occurrence.children[0].tag.endswith(structure_suffix):
            return None
        nodes.append(occurrence.children[0])
    return nodes or None


def _surface_fill(library: SignLibrary, structure_node: RawNode) -> Fill:
    """`Fill` from a `FontSymbol_Surface` item's own `FillColor` reference."""
    fillcolor_node = next((c for c in structure_node.children if c.tag == "FillColor"), None)
    color, opacity = library._color_from_ref_node(fillcolor_node) if fillcolor_node is not None else (None, None)
    return Fill(color=color, opacity=opacity)


def _boundary_ring(node: RawNode) -> list[tuple[float, float]] | None:
    """Read a single-boundary `SS_Surface` as a straight-segment ring, without the repeated closing vertex.

    `None` for anything but one BOUNDARY of one all-COORD POLYLINE (ARC segments, holes, custom LINE FORMs).
    """
    surface = find_child(node, "SURFACE")
    boundaries = [c for c in surface.children if geom_tag(c) in BOUNDARY_TAGS] if surface is not None else []
    if len(boundaries) != 1:
        return None
    polyline = find_child(boundaries[0], "POLYLINE")
    if polyline is None or not polyline.children:
        return None
    ring: list[tuple[float, float]] = []
    for segment in polyline.children:
        position = read_coord(segment)
        if position is None:
            return None
        ring.append((position[0], position[1]))
    if len(ring) > 1 and math.dist(ring[0], ring[-1]) < _SHAPE_TOLERANCE:
        ring.pop()
    return ring or None


def _closed_polyline_ring(node: RawNode) -> list[tuple[float, float]] | None:
    """Read an all-COORD `SS_Polyline` that closes on itself as a ring, without the repeated closing vertex."""
    polyline = find_child(node, "POLYLINE")
    if polyline is None or not polyline.children:
        return None
    ring: list[tuple[float, float]] = []
    for segment in polyline.children:
        position = read_coord(segment)
        if position is None:
            return None
        ring.append((position[0], position[1]))
    if len(ring) < 4 or math.dist(ring[0], ring[-1]) >= _SHAPE_TOLERANCE:
        return None
    return ring[:-1]


def _square_from_ring(ring: list[tuple[float, float]] | None) -> tuple[float, float] | None:
    """Read a symbol ring as `(side, orientation in degrees)` - a square, possibly rotated (this renders a diamond).

    Oblong rectangles are excluded: the CartoSym vs INTERLIS (y-up) rotation sign is only immaterial for a square.
    """
    if ring is None or len(ring) != 4:
        return None
    edges = [(ring[(i + 1) % 4][0] - ring[i][0], ring[(i + 1) % 4][1] - ring[i][1]) for i in range(4)]
    sides = [math.hypot(*edge) for edge in edges]
    if sides[0] < _SHAPE_TOLERANCE or any(abs(side - sides[0]) > _SHAPE_TOLERANCE for side in sides):
        return None
    if any(abs(edges[i][0] * edges[i + 1][0] + edges[i][1] * edges[i + 1][1]) > _SHAPE_TOLERANCE for i in range(3)):
        return None
    # Modulo 90 degrees: any of the 4 edges describes the same square.
    return sides[0], math.degrees(math.atan2(edges[0][1], edges[0][0])) % 90.0


def _well_known_from_ring(ring: list[tuple[float, float]] | None) -> tuple[tuple, float, float] | None:
    """Read a symbol ring as one of pycartosym's well-known shapes (`triangle`, `star`, `cross`, `x`).

    Returns `(canonical nodes, size, clockwise orientation in degrees)` or `None`, matched whatever the ring's
    offset, start vertex and winding; the smallest matching rotation is kept.
    """
    if ring is None or len(ring) < 3:
        return None
    best: tuple[tuple, float, float] | None = None
    for unit in WELL_KNOWN_SHAPES.values():
        if len(unit) != len(ring):
            continue
        n = len(unit)
        ux, uy = sum(p[0] for p in unit) / n, sum(p[1] for p in unit) / n
        rx, ry = sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
        centred_unit = [(x - ux, y - uy) for x, y in unit]
        centred_ring = [(x - rx, y - ry) for x, y in ring]
        unit_radius = math.sqrt(sum(x * x + y * y for x, y in centred_unit) / n)
        size = math.sqrt(sum(x * x + y * y for x, y in centred_ring) / n) / unit_radius
        if size < _SHAPE_TOLERANCE:
            continue
        tolerance = 1e-3 * size
        for sequence in (centred_ring, centred_ring[::-1]):
            for start in range(n):
                candidate = sequence[start:] + sequence[:start]
                theta = math.atan2(candidate[0][1], candidate[0][0]) - math.atan2(
                    centred_unit[0][1], centred_unit[0][0]
                )
                cos, sin = math.cos(theta), math.sin(theta)
                if all(
                    math.dist(((x * cos - y * sin) * size, (x * sin + y * cos) * size), point) < tolerance
                    for (x, y), point in zip(centred_unit, candidate)
                ):
                    orientation = round(-math.degrees(theta), 2) % 360  # transfers round coordinates
                    if best is None or min(orientation, 360 - orientation) < min(best[2], 360 - best[2]):
                        best = (unit, float(f"{size:.5g}"), orientation)
    return best


def _polygon_mark_graphic(
    library: SignLibrary, structure_node: RawNode, scale: float
) -> RectangleGraphic | ClosedPathGraphic | None:
    """A square or well-known mark: filled from a `FontSymbol_Surface`, outlined from a closed `FontSymbol_Polyline`."""
    geometry_node = next((c for c in structure_node.children if c.tag == "Geometry"), None)
    if geometry_node is None:
        return None
    if structure_node.tag.endswith("FontSymbol_Polyline"):
        ring = _closed_polyline_ring(geometry_node)
        color_node = next((c for c in structure_node.children if c.tag == "Color"), None)
        color, opacity = library._color_from_ref_node(color_node) if color_node is not None else (None, None)
        paint: dict[str, Any] = {"outline": ShapeOutline(color=color, opacity=opacity)}
    else:
        ring = _boundary_ring(geometry_node)
        paint = {"fill": _surface_fill(library, structure_node)}
    square = _square_from_ring(ring)
    if square is not None:
        side, orientation = square
        return RectangleGraphic(
            type="Rectangle",
            width=meters(side * scale),
            height=meters(side * scale),
            transform=Transform2D(orientation=orientation) if orientation > _SHAPE_TOLERANCE else None,
            **paint,
        )
    shape = _well_known_from_ring(ring)
    if shape is not None:
        nodes, size, orientation = shape
        return ClosedPathGraphic(
            type="ClosedPath",
            nodes=[UnitPoint(x=meters(x * size * scale), y=meters(y * size * scale)) for x, y in nodes],
            transform=Transform2D(orientation=orientation) if min(orientation, 360 - orientation) > 0 else None,
            **paint,
        )
    return None


def font_symbol_geometry_to_polygon_graphics(
    library: SignLibrary, symbol_obj: XtfObject, *, scale: float = 1.0
) -> list[RectangleGraphic | ClosedPathGraphic] | None:
    """Build one native mark per `FontSymbol_Surface`/closed `FontSymbol_Polyline` item that is a square or an SE
    `triangle`, else `None`.

    Gives a native `se:Mark` (square, diamond, triangle) instead of the inline-SVG `ImageGraphic` fallback.
    """
    structure_nodes = _font_symbol_items(symbol_obj, ("FontSymbol_Surface", "FontSymbol_Polyline"))
    if structure_nodes is None:
        return None
    graphics: list[RectangleGraphic | ClosedPathGraphic] = []
    for structure_node in structure_nodes:
        graphic = _polygon_mark_graphic(library, structure_node, scale)
        if graphic is None:
            return None
        graphics.append(graphic)
    return graphics or None


def font_symbol_geometry_to_circle_graphics(
    library: SignLibrary, symbol_obj: XtfObject, *, scale: float = 1.0
) -> list[CircleGraphic] | None:
    """Build one `CircleGraphic` per circular `FontSymbol_Surface` item in a `Font.Type = symbol` `FontSymbol`.

    All-or-nothing: every item must be a `FontSymbol_Surface` with a real circular boundary (see `_font_symbol_items`).
    """
    structure_nodes = _font_symbol_items(symbol_obj, "FontSymbol_Surface")
    if structure_nodes is None:
        return None
    graphics: list[CircleGraphic] = []
    for structure_node in structure_nodes:
        geometry_node = next((c for c in structure_node.children if c.tag == "Geometry"), None)
        radius = _circle_radius_from_surface(geometry_node) if geometry_node is not None else None
        if radius is None:
            return None
        fill = _surface_fill(library, structure_node)
        graphics.append(CircleGraphic(type="Circle", radius=meters(radius * scale), fill=fill))
    return graphics or None


def font_symbol_geometry_to_image_graphics(symbol_obj: XtfObject) -> list[ImageGraphic] | None:
    """Build one `ImageGraphic` per `FontSymbol_Image` item in a `Font.Type = symbol` `FontSymbol`.

    `FontSymbol_Image` (`Uri: TEXT`) is a project extension of the standard model; all-or-nothing like the other
    builders. No MIME type or hot spot is set, as the model has no attribute to source them from.
    """
    structure_nodes = _font_symbol_items(symbol_obj, "FontSymbol_Image")
    if structure_nodes is None:
        return None
    graphics: list[ImageGraphic] = []
    for structure_node in structure_nodes:
        uri_node = next((c for c in structure_node.children if c.tag == "Uri"), None)
        if uri_node is None or uri_node.text is None:
            return None
        graphics.append(ImageGraphic(image=Resource(uri=uri_node.text)))
    return graphics or None


def _rgb_to_hex(color: RGBColor | None) -> str:
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}" if color is not None else "black"


def _svg_arc_command(prev: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]) -> str | None:
    """SVG elliptical-arc `A` command from `prev` to `end` through `mid`, all in SVG (Y-down) space.

    Radius and flags come from the circumcircle of the 3 points, not the wire `<R>`; `None` if they are collinear.
    """
    (ax, ay), (bx, by), (cx, cy) = prev, mid, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    if radius < 1e-9:
        return None
    a0 = math.atan2(ay - uy, ax - ux)
    r1 = (math.atan2(by - uy, bx - ux) - a0) % (2 * math.pi)
    r2 = (math.atan2(cy - uy, cx - ux) - a0) % (2 * math.pi)
    # y-down: increasing atan2 angle is clockwise (sweep 1); r1 < r2 means that direction reaches `mid` before `end`.
    sweep, span = (1, r2) if r1 < r2 else (0, (2 * math.pi) - r2)
    large_arc = 1 if span > math.pi else 0
    return f"A {radius:.6g} {radius:.6g} 0 {large_arc} {sweep} {cx:.6g} {cy:.6g}"


def _svg_path_d(node: RawNode) -> str | None:
    """Build an SVG path `d` string from a POLYLINE SegmentSequence (COORD start, then COORD/ARC segments).

    Y is negated throughout: SVG is y-down, an INTERLIS symbol space is y-up.
    """
    if geom_tag(node) != "POLYLINE" or not node.children:
        return None
    segments = node.children
    if geom_tag(segments[0]) != "COORD":
        return None
    start = read_coord(segments[0])
    if start is None:
        return None
    current = (start[0], -start[1])
    parts = [f"M {current[0]:.6g} {current[1]:.6g}"]
    for seg in segments[1:]:
        tag = geom_tag(seg)
        if tag == "COORD":
            pos = read_coord(seg)
            if pos is None:
                return None
            current = (pos[0], -pos[1])
            parts.append(f"L {current[0]:.6g} {current[1]:.6g}")
        elif tag == "ARC":
            arc = read_arc(seg)
            if arc is None:
                return None
            mid = (arc[0][0], -arc[0][1])
            end = (arc[1][0], -arc[1][1])
            command = _svg_arc_command(current, mid, end)
            if command is None:
                return None
            parts.append(command)
            current = end
        else:
            return None  # custom LINE FORM segment - not representable here
    return " ".join(parts)


def _svg_boundary_path_d(node: RawNode) -> str | None:
    """Like `_svg_path_d`, for a BOUNDARY/EXTERIOR/INTERIOR wrapping a single POLYLINE, closed with `Z`."""
    polyline = find_child(node, "POLYLINE")
    if polyline is None:
        return None
    d = _svg_path_d(polyline)
    return f"{d} Z" if d is not None else None


def font_symbol_geometry_to_svg_data_uri(library: SignLibrary, symbol_obj: XtfObject) -> str | None:
    """Render a `FontSymbol`'s composite geometry as an inline `data:image/svg+xml` URI.

    Covers what the circle builder does not. The fixed `viewBox="-0.5 -0.5 1 1"` follows the model's "size 1.0,
    scale 1.0" convention; a surface's 2nd+ boundary is a hole (`evenodd`). `LineAttrs` is not read (hairline stroke).
    """
    occurrences = symbol_obj.attributes.get("Geometry") or []
    if not occurrences:
        return None
    layers: list[str] = []
    for occurrence in occurrences:
        if not occurrence.children:
            return None
        structure_node = occurrence.children[0]
        geometry_node = next((c for c in structure_node.children if c.tag == "Geometry"), None)
        if geometry_node is None:
            return None
        if structure_node.tag.endswith("FontSymbol_Polyline"):
            polyline_node = find_child(geometry_node, "POLYLINE")
            d = _svg_path_d(polyline_node) if polyline_node is not None else None
            if d is None:
                return None
            color_node = next((c for c in structure_node.children if c.tag == "Color"), None)
            color, _opacity = library._color_from_ref_node(color_node) if color_node is not None else (None, None)
            layers.append(f'<path d="{d}" fill="none" stroke="{_rgb_to_hex(color)}"/>')
        elif structure_node.tag.endswith("FontSymbol_Surface"):
            surface_node = find_child(geometry_node, "SURFACE")
            boundaries = [c for c in surface_node.children if geom_tag(c) in BOUNDARY_TAGS] if surface_node else []
            if not boundaries:
                return None
            boundary_ds = [_svg_boundary_path_d(b) for b in boundaries]
            if any(d is None for d in boundary_ds):
                return None
            fillcolor_node = next((c for c in structure_node.children if c.tag == "FillColor"), None)
            fill, _opacity = (
                library._color_from_ref_node(fillcolor_node) if fillcolor_node is not None else (None, None)
            )
            joined_d = " ".join(d for d in boundary_ds if d is not None)
            layers.append(f'<path d="{joined_d}" fill="{_rgb_to_hex(fill)}" fill-rule="evenodd"/>')
        else:
            return None
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-0.5 -0.5 1 1">{"".join(layers)}</svg>'
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def symbol_sign_object_to_marker(library: SignLibrary, obj: XtfObject) -> Marker:
    """Build a `Marker` from a real `SymbolSign` data object (`Color`/`Symbol` resolved within the same SIGN BASKET).

    `Font.Type = text` builds a glyph `TextGraphic`; `symbol` tries native circle, polygon and image builders, then
    an inline-SVG render of any remaining mix.
    """
    # Only `opacity` is applied: `TextGraphic` has no color field to receive `Color`.
    _color, opacity = library.resolve_color(obj, "Color")
    elements: list[Any] | None = None
    symbol_obj = library.resolve_ref(obj, "Symbol")
    if symbol_obj is not None:
        font_obj = library.resolve_ref(symbol_obj, "Font")
        font_type = library.scalar(font_obj, "Type") if font_obj is not None else None
        if font_type == "text" and font_obj is not None:
            ucs4 = library.scalar(symbol_obj, "UCS4")
            if ucs4:
                elements = [
                    font_symbol_text_to_graphic(character=chr(int(ucs4)), font_face=library.scalar(font_obj, "Name"))
                ]
        elif font_type == "symbol":
            scale_text = library.scalar(obj, "Scale")
            scale = float(scale_text) if scale_text else 1.0
            elements = (
                font_symbol_geometry_to_circle_graphics(library, symbol_obj, scale=scale)
                or font_symbol_geometry_to_polygon_graphics(library, symbol_obj, scale=scale)
                or font_symbol_geometry_to_image_graphics(symbol_obj)
            )
            if elements is None:
                svg_uri = font_symbol_geometry_to_svg_data_uri(library, symbol_obj)
                if svg_uri is not None:
                    elements = [ImageGraphic(image=Resource(uri=svg_uri, type="image/svg+xml"))]
    return Marker(elements=elements, opacity=opacity)


def text_sign_object_to_font_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    """Build `text_sign_to_label` kwargs (`font_face`/`height`/`italic`/`color`/`opacity`) from a `TextSign` object.

    `Underlined` is deliberately not read: pycartosym's SLD writer raises `NotImplementedError` for `Font.underline`.
    """
    kwargs: dict[str, Any] = {}
    font_obj = library.resolve_ref(obj, "Font")
    if font_obj is not None:
        face = library.scalar(font_obj, "Name")
        if face is not None:
            kwargs["font_face"] = face
    height = library.scalar(obj, "Height")
    if height is not None:
        kwargs["height"] = float(height)
    slanted = library.scalar(obj, "Slanted")
    if slanted is not None:
        kwargs["italic"] = slanted == "true"
    color, opacity = library.resolve_color(obj, "Color")
    if color is not None:
        kwargs["color"] = color
    if opacity is not None:
        kwargs["opacity"] = opacity
    return kwargs


def surface_sign_object_to_hatch_pattern(library: SignLibrary, obj: XtfObject) -> ImageGraphic | None:
    """Build a `Fill.pattern` `ImageGraphic` from `SurfaceSign.HatchSymb` (a `PolylineSign` reference).

    `se:GraphicFill` accepts only `Dot`/`Circle`/`Rectangle`/`Image` graphics, so the hachure is one horizontal
    line on a `viewBox="-0.5 -0.5 1 1"` tile, rotated by `HatchAng`. `HatchOffset`/`HatchOrg` have no SE target.
    """
    polyline_obj = library.resolve_ref(obj, "HatchSymb")
    if polyline_obj is None:
        return None
    stroke = polyline_sign_object_to_stroke(library, polyline_obj)
    color = stroke.color if isinstance(stroke.color, RGBColor) else None
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-0.5 -0.5 1 1">'
        f'<line x1="-0.5" y1="0" x2="0.5" y2="0" stroke="{_rgb_to_hex(color)}"/></svg>'
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    hatch_ang = library.scalar(obj, "HatchAng")
    transform = Transform2D(orientation=float(hatch_ang)) if hatch_ang else None
    resource = Resource(uri=f"data:image/svg+xml;base64,{encoded}", type="image/svg+xml")
    return ImageGraphic(image=resource, transform=transform)


def surface_sign_object_to_fill(library: SignLibrary, obj: XtfObject) -> Fill:
    """Build a `Fill` from `SurfaceSign.FillColor`/`HatchSymb` - `Clip`/`HatchOrg`/`HatchOffset` not resolved."""
    color, opacity = library.resolve_color(obj, "FillColor")
    pattern = surface_sign_object_to_hatch_pattern(library, obj)
    return surface_sign_to_fill(fill_color=color, opacity=opacity, pattern=pattern)


def surface_sign_object_border_to_stroke(library: SignLibrary, obj: XtfObject) -> Stroke | None:
    """Build a `Stroke` from a `SurfaceSign` data object's `Border` (`SurfaceSignBorderAssoc`, a `PolylineSign` REF).

    The border is a real `PolylineSign`, so `polyline_sign_object_to_stroke` is reused.
    """
    border_obj = library.resolve_ref(obj, "Border")
    return polyline_sign_object_to_stroke(library, border_obj) if border_obj is not None else None


def polyline_sign_object_to_stroke(library: SignLibrary, obj: XtfObject) -> Stroke:
    """Build a `Stroke` from a real `PolylineSign` data object's `Color` and `Style` (width/join/cap/dash pattern).

    `PolylineSign.Color` and the `LineStyle`'s own `Color` are separate associations: the former wins, the latter
    is the fallback.
    """
    color, opacity = library.resolve_color(obj, "Color")
    width = join = cap = dashes = None
    style_obj = library.resolve_ref(obj, "Style")
    if style_obj is not None and style_obj.qualified_class.endswith("LineStyle_Pattern"):
        return _pattern_stroke(library, style_obj)
    if style_obj is not None:
        if color is None:
            color, opacity = library.resolve_color(style_obj, "Color")
        attrs_obj = library.resolve_ref(style_obj, "LineAttrs")
        if attrs_obj is not None:
            width_text = library.scalar(attrs_obj, "Width")
            width = float(width_text) if width_text is not None else None
            join = library.scalar(attrs_obj, "Join")
            cap = library.scalar(attrs_obj, "Caps")
        if style_obj.qualified_class.endswith("LineStyle_Dashed"):
            dashes = library.dash_pattern(style_obj)
    return polyline_sign_to_stroke(color=color, opacity=opacity, width=width, join=join, cap=cap, dash_pattern=dashes)


def _pattern_stroke(library: SignLibrary, style_obj: XtfObject) -> Stroke:
    """A `LineStyle_Pattern` as a `Stroke.pattern` repeated every `PLength` along the line.

    Only the first `Pattern_Symbol` is used; further symbols and `Offset` have no equivalent in one `Stroke`.
    """
    occurrence = (style_obj.attributes.get("Symbols") or [None])[0]
    symbol_node = occurrence.children[0] if occurrence is not None and occurrence.children else None
    fields = {c.tag: c for c in symbol_node.children} if symbol_node is not None else {}
    font_ref = fields.get("FontSymbRef")
    tid = extract_reference(font_ref) if font_ref is not None else None
    font_symbol = library.by_tid.get(tid) if tid else None
    if font_symbol is None:
        return Stroke()
    scale = float(fields["Scale"].text) if "Scale" in fields and fields["Scale"].text else 1.0
    graphics = font_symbol_geometry_to_circle_graphics(
        library, font_symbol, scale=scale
    ) or font_symbol_geometry_to_polygon_graphics(library, font_symbol, scale=scale)
    if not graphics:
        return Stroke()
    graphic = graphics[0]
    if "ColorRef" in fields:
        color, opacity = library._color_from_ref_node(fields["ColorRef"])
        graphic.fill = Fill(color=color, opacity=opacity)
    period = float(library.scalar(style_obj, "PLength") or 0)
    dist = float(fields["Dist"].text) if "Dist" in fields and fields["Dist"].text else None
    return Stroke(pattern=graphic, pattern_gap=max(period - scale, 0.0), pattern_initial_gap=dist)


def _symbol_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    return {"marker": symbol_sign_object_to_marker(library, obj)}


def _polyline_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    return {"stroke": polyline_sign_object_to_stroke(library, obj)}


def _surface_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    fill = surface_sign_object_to_fill(library, obj)
    # A border-only surface carries no fill at all: an empty `se:Fill` would be read as the default grey fill.
    if fill.color is not None or fill.pattern is not None:
        kwargs["fill"] = fill
    stroke = surface_sign_object_border_to_stroke(library, obj)
    if stroke is not None:
        kwargs["stroke"] = stroke
    return kwargs


def raster_sign_object_to_color_map(library: SignLibrary, obj: XtfObject) -> list[list[Any]] | None:
    """Build a `Symbolizer.color_map` from `RasterSign.ColorMapEntries` (a `LIST OF RasterColorMapEntry`).

    `RasterSign` is a project extension. Entries are plain `[value, color]` lists in wire order; `Label` is omitted
    because the default `sld` dialect's `se:Categorize` has no label slot.
    """
    occurrences = obj.attributes.get("ColorMapEntries") or []
    entries: list[list[Any]] = []
    for occurrence in occurrences:
        if not occurrence.children:
            return None
        entry_node = occurrence.children[0]
        value_node = next((c for c in entry_node.children if c.tag == "Value"), None)
        color_node = next((c for c in entry_node.children if c.tag == "Color"), None)
        if value_node is None or value_node.text is None or color_node is None:
            return None
        color, _opacity = library._color_from_ref_node(color_node)
        if color is None:
            return None
        entries.append([float(value_node.text), color])
    return entries or None


def raster_sign_object_to_channel_selection(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    """Build `Symbolizer.color_channels`/`single_channel` kwargs from `RasterSign`'s `*Band` attributes.

    Mutually exclusive in pycartosym's writer; `GrayBand` wins if both are set. Band names are plain `TEXT`.
    """
    gray = library.scalar(obj, "GrayBand")
    if gray is not None:
        return {"single_channel": {"property": gray}}
    red, green, blue = (library.scalar(obj, name) for name in ("RedBand", "GreenBand", "BlueBand"))
    if red is not None and green is not None and blue is not None:
        return {"color_channels": [{"property": red}, {"property": green}, {"property": blue}]}
    return {}


def raster_sign_object_to_hill_shading(library: SignLibrary, obj: XtfObject) -> dict[str, Any] | None:
    """Build a `Symbolizer.hill_shading` dict from `RasterSign.HillShadeFactor`.

    `sun`/`colorMap`/`opacityMap` are not modelled: SE 1.1.0 rejects them in pycartosym's writer.
    """
    factor = library.scalar(obj, "HillShadeFactor")
    return {"factor": float(factor)} if factor is not None else None


def _raster_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    color_map = raster_sign_object_to_color_map(library, obj)
    if color_map is not None:
        kwargs["color_map"] = color_map
    kwargs.update(raster_sign_object_to_channel_selection(library, obj))
    hill_shading = raster_sign_object_to_hill_shading(library, obj)
    if hill_shading is not None:
        kwargs["hill_shading"] = hill_shading
    return kwargs


_SIGN_OBJECT_BUILDERS = {
    _MARKER_SIGN_CLASS: _symbol_sign_kwargs,
    _FILL_SIGN_CLASS: _surface_sign_kwargs,
    _STROKE_SIGN_CLASS: _polyline_sign_kwargs,
    _RASTER_SIGN_CLASS: _raster_sign_kwargs,
}


def _and_selector(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any] | None:
    """Conjunction of two optional CQL2-JSON selectors."""
    if left is None or right is None:
        return left if right is None else right
    return {"op": "and", "args": [left, right]}


def _with_feature_type(selector: dict[str, Any] | None, feature_type: str | None) -> dict[str, Any] | None:
    """Prepend a `dataLayer.id` conjunct - pycartosym's own writer pulls this back out as `se:FeatureTypeName`."""
    if feature_type is None:
        return selector
    return _and_selector({"op": "=", "args": [{"sysId": "dataLayer.id"}, feature_type]}, selector)


class ScaleRangeError(ValueError):
    """A `MinScaleDenominator`/`MaxScaleDenominator` pair that selects no scale at all."""


def scale_selector(params: dict[str, Any]) -> dict[str, Any] | None:
    """`viz.sd` conjunction for a rule's `MinScaleDenominator`/`MaxScaleDenominator` parameters.

    Visible when `denominator >= Min` and `denominator < Max`; absent or 0 means no bound. pycartosym's SLD
    writer turns these back into `se:MinScaleDenominator`/`se:MaxScaleDenominator`.
    """
    bounds: dict[str, float] = {}
    for name in ("MinScaleDenominator", "MaxScaleDenominator"):
        value = params.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NotImplementedError(f"{name} must be a literal number, got {value!r}")
        bounds[name] = value
    low, high = bounds.get("MinScaleDenominator", 0), bounds.get("MaxScaleDenominator", 0)
    if low and high and low >= high:
        raise ScaleRangeError(
            f"MinScaleDenominator {low:g} is not below MaxScaleDenominator {high:g}: no scale selected"
        )
    selector = None
    if low:
        selector = {"op": ">=", "args": [{"sysId": "viz.sd"}, low]}
    if high:
        selector = _and_selector(selector, {"op": "<", "args": [{"sysId": "viz.sd"}, high]})
    return selector


def styling_rule_from_drawing_rule(
    drawing_rule: MetaInstance, sign_library: SignLibrary | None = None, feature_type: str | None = None
) -> StylingRule:
    """Build one pycartosym `StylingRule` from an INTERLIS `DrawingRule`.

    `WHERE` becomes a CQL2 filter (with `feature_type` as a layer condition), `Priority` the z-order, and the sign
    reference is resolved through `sign_library`. Only the first `CondSignParamAssignment` is used.
    """
    conditions = drawing_rule.Rule if isinstance(drawing_rule.Rule, list) else [drawing_rule.Rule]
    cond = conditions[0]
    where = getattr(cond, "Where", None)

    raw_assignments = cond.Assignments if isinstance(cond.Assignments, list) else [cond.Assignments]
    params: dict[str, Any] = {}
    sign_object: XtfObject | None = None
    for assignment in raw_assignments:
        if assignment.Param == "Geometry":
            continue  # which attribute carries the geometry, not a Symbolizer field
        if assignment.Param == "Sign":
            target = assignment.Assignment
            name = getattr(target, "Name", None) if isinstance(target, MetaInstance) else None
            if sign_library is not None and name is not None:
                sign_object = sign_library.by_name.get(name)
            continue
        params[assignment.Param] = to_cql2(assignment.Assignment)
    selector = _with_feature_type(
        _and_selector(to_cql2(where) if where is not None else None, scale_selector(params)), feature_type
    )

    symbolizer_kwargs: dict[str, Any] = {}
    if "Priority" in params:
        symbolizer_kwargs["z_order"] = symbolizer_z_order(params["Priority"])
    sign_class = getattr(getattr(drawing_rule, "Class", None), "Name", None)
    if sign_object is not None and sign_library is not None and sign_class in _SIGN_OBJECT_BUILDERS:
        symbolizer_kwargs.update(_SIGN_OBJECT_BUILDERS[sign_class](sign_library, sign_object))
    if sign_class == _TEXT_SIGN_CLASS and "Txt" in params:
        font_kwargs = (
            text_sign_object_to_font_kwargs(sign_library, sign_object)
            if sign_object is not None and sign_library is not None
            else {}
        )
        symbolizer_kwargs["label"] = text_sign_to_label(
            text=_text_with_suffix(params),
            rotation=params.get("Rotation"),
            h_alignment=params.get("HAli"),
            v_alignment=params.get("VAli"),
            **font_kwargs,
        )

    return StylingRule(
        name=getattr(drawing_rule, "Name", None),
        selector=selector,
        symbolizer=Symbolizer(**symbolizer_kwargs),
    )


def _text_with_suffix(params: dict[str, Any]) -> Any:
    """`Txt`, or `Txt` + `Separator` + `Suffix` when the sign interface is extended with those two parameters.

    INTERLIS cannot concatenate text, so such a rule sets `Suffix` on a `TextSign (EXTENDED)`; the no-suffix case is
    a separate rule (`WHERE NOT (DEFINED(...))`) since an SLD label cannot hold a conditional.
    """
    txt, suffix = params.get("Txt"), params.get("Suffix")
    if suffix is None:
        return txt
    separator = params.get("Separator")
    parts = [txt] + ([separator] if separator not in (None, "") else []) + [suffix]
    return {"op": "concatenate", "args": parts}


def _cited_attributes(node: Any, seen: set[int]) -> Iterator[str]:
    """Every attribute name (first step of a plain path) cited anywhere in an expression or assignment value."""
    if isinstance(node, list):
        for item in node:
            yield from _cited_attributes(item, seen)
    elif isinstance(node, MetaInstance) and id(node) not in seen:
        seen.add(id(node))
        if node._qualified_class.endswith("PathOrInspFactor"):
            head = next(iter(node.PathEls or []), None)
            if head is not None and getattr(head, "Kind", None) in ("ReferenceAttr", "Attribute"):
                yield head.Ref
        for value in vars(node).values():
            yield from _cited_attributes(value, seen)


def graphic_attribute_problems(
    graphic: MetaInstance, symbol_table: SymbolTable, repository: ModelRepository | None = None
) -> list[str]:
    """One message per attribute a `GRAPHIC`'s rules cite (in `WHERE` or as an assigned value) that its base lacks.

    Embedded association roles count as attributes, including those of an association from an imported model
    (hence `repository`).
    """
    base = getattr(graphic, "Base", None)
    if base is None:
        return []
    known = set(attributes_of(base))
    if not base._qualified_class.endswith(".View"):
        tables = [symbol_table, *(repository.loaded_models().values() if repository is not None else ())]
        for table in tables:
            known |= set(schema_members_of(base, table))
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    problems: list[str] = []
    for rule in rules:
        for condition in rule.Rule if isinstance(rule.Rule, list) else [rule.Rule]:
            parts = [getattr(condition, "Where", None), condition.Assignments]
            for name in dict.fromkeys(_cited_attributes(parts, set())):
                if name not in known:
                    problems.append(
                        f"DrawingRule {getattr(rule, 'Name', None)!r} cites attribute {name!r}, "
                        f"which {getattr(base, 'Name', None)!r} does not declare"
                    )
    return problems


def _priority_sort_key(rule: StylingRule) -> tuple[bool, float]:
    """Ascending `Priority` draws last/on top (this project's convention); a non-literal `Priority` sorts last."""
    z_order = rule.symbolizer.z_order if rule.symbolizer else None
    return (not isinstance(z_order, (int, float)), z_order if isinstance(z_order, (int, float)) else 0.0)


def _add_visualization_passes(styling_rules: list[StylingRule]) -> None:
    """AND a `viz.pass` per distinct `Priority` (dense rank) into each selector: every feature of a lower Priority is
    drawn before any of a higher one, across features, which rule order inside one SLD FeatureTypeStyle cannot express.

    Left out when a single Priority is used, or when one is not a literal number (no rank to give it).
    """
    priorities = [rule.symbolizer.z_order if rule.symbolizer else None for rule in styling_rules]
    if not all(isinstance(p, (int, float)) for p in priorities) or len(set(priorities)) < 2:
        return
    rank = {p: index for index, p in enumerate(sorted(set(priorities)))}
    for rule, priority in zip(styling_rules, priorities):
        pass_eq = {"op": "=", "args": [{"sysId": "viz.pass"}, rank[priority]]}
        rule.selector = _and_selector(rule.selector, pass_eq)


def graphic_to_style(graphic: MetaInstance, sign_library: SignLibrary | None = None) -> Style:
    """Build one pycartosym `Style` from a built INTERLIS `GRAPHIC`, one `StylingRule` per `DrawingRule`.

    Rules are sorted by `Priority` ascending, and each distinct Priority becomes a `viz.pass`.
    """
    feature_type = getattr(getattr(graphic, "Base", None), "Name", None)
    drawing_rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    styling_rules = [
        styling_rule_from_drawing_rule(rule, sign_library=sign_library, feature_type=feature_type)
        for rule in drawing_rules
    ]
    styling_rules.sort(key=_priority_sort_key)
    _add_visualization_passes(styling_rules)
    name = getattr(graphic, "Name", None)
    return Style(styling_rules=styling_rules, metadata=Metadata(title=name) if name else None)


def write_sld(style: Style) -> str:
    """Write `style` to SLD, working around the SLD writer's lack of attribute-driven text rotation.

    A `PropertyRef` rotation is stripped before writing, then re-added to the XML as `se:Rotation/ogc:PropertyName`.
    """
    pending: dict[str, str] = {}
    patched_rules = []
    for rule in style.styling_rules:
        label = rule.symbolizer.label if rule.symbolizer else None
        orientation = None
        if label is not None and label.elements:
            transform = label.elements[0].transform
            orientation = transform.orientation if transform is not None else None
        if isinstance(orientation, PropertyRef) and rule.name and label is not None:
            pending[rule.name] = orientation.property
            new_graphic = label.elements[0].model_copy(update={"transform": None})
            new_label = label.model_copy(update={"elements": [new_graphic, *label.elements[1:]]})
            new_symbolizer = rule.symbolizer.model_copy(update={"label": new_label})
            rule = rule.model_copy(update={"symbolizer": new_symbolizer})
        patched_rules.append(rule)

    raw = get_codec("sld").write(style.model_copy(update={"styling_rules": patched_rules}))
    xml = raw if isinstance(raw, str) else raw.decode()
    return _inject_dynamic_text_rotations(xml, pending) if pending else xml


def _inject_dynamic_text_rotations(sld_xml: str, rotations: dict[str, str]) -> str:
    root = etree.fromstring(sld_xml.encode("utf-8"))
    for rule_el in root.iter(f"{{{_SE_NS}}}Rule"):
        title_el = rule_el.find(f"{{{_SE_NS}}}Description/{{{_SE_NS}}}Title")
        attr_name = rotations.get(title_el.text) if title_el is not None else None
        if attr_name is None:
            continue
        text_symbolizer = rule_el.find(f"{{{_SE_NS}}}TextSymbolizer")
        if text_symbolizer is None:
            continue
        placement = text_symbolizer.find(f"{{{_SE_NS}}}LabelPlacement")
        if placement is None:
            placement = etree.SubElement(text_symbolizer, f"{{{_SE_NS}}}LabelPlacement")
        point_placement = placement.find(f"{{{_SE_NS}}}PointPlacement")
        if point_placement is None:
            point_placement = etree.SubElement(placement, f"{{{_SE_NS}}}PointPlacement")
        # SE 1.1.0 sequence is AnchorPoint?, Displacement?, Rotation?, so Rotation goes last.
        rotation_el = etree.SubElement(point_placement, f"{{{_SE_NS}}}Rotation")
        etree.SubElement(rotation_el, f"{{{_OGC_NS}}}PropertyName").text = attr_name
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8").decode()
