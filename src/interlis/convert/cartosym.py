"""Convert a resolved INTERLIS `StandardSymbology` sign instance into a pycartosym `Symbolizer`/`StylingRule`.

`FontSymbol`'s composite geometry (`Font.Type = symbol`) tries, in order:
a circular `FontSymbol_Surface` composite (native `CircleGraphic`s, see
`font_symbol_geometry_to_circle_graphics`), a square one (native
`RectangleGraphic`s, which is also how a diamond mark is expressed - see
`font_symbol_geometry_to_rectangle_graphics`), an all-`FontSymbol_Image`
composite (see `font_symbol_geometry_to_image_graphics`), then a general
SVG render of any mix of `FontSymbol_Polyline`/`FontSymbol_Surface`
(see `font_symbol_geometry_to_svg_data_uri`). `Fill.hatch`/`Stroke.casing`/
`centerLine`/`pattern` are not attempted at all - they raise
`NotImplementedError` in pycartosym's current SLD writer regardless of
dialect (confirmed empirically), so no value built here for them could
ever be written out.
"""

import base64
import math
from typing import Any

from lxml import etree
from pycartosym import get_codec
from pycartosym.models.styles import Metadata, Style, StylingRule, Symbolizer
from pycartosym.models.symbolizers import (
    CircleGraphic,
    Fill,
    Font,
    ImageGraphic,
    Label,
    Marker,
    RectangleGraphic,
    Resource,
    Stroke,
    TextAlignment,
    TextGraphic,
    Transform2D,
)
from pycartosym.models.types import Angle, AngleUnit, RGBColor, UnitType, UnitValue
from pycartosym.models.value_expressions import PropertyRef

from interlis.convert.color import lch_to_srgb
from interlis.convert.cql2 import to_cql2
from interlis.convert.jsonfg import _read_arc, _read_coord
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject
from interlis.xtf.validate import _BOUNDARY_TAGS, _extract_reference, _find_child, _geom_tag

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
# VALIGNMENT has 5 levels (Top/Cap/Half/Base/Bottom), pycartosym's v_alignment
# only 3 (top/middle/bottom) - Cap/Base approximate to the nearest of the
# two, losing the box-vs-baseline distinction (no finer SLD/SE target
# exists: se:AnchorPoint only has 3 vertical positions).
_V_ALIGNMENT = {"Top": "top", "Cap": "top", "Half": "middle", "Base": "bottom", "Bottom": "bottom"}


def color_to_rgb(lum: float, c: float, h: float) -> RGBColor:
    """Convert an INTERLIS `Color` (`L`/`C`/`H`) to `RGBColor` - `T` (transparency) is the caller's job, per site."""
    r, g, b = lch_to_srgb(lum, c, h)
    return RGBColor(r=r, g=g, b=b)


def meters(value: float | None) -> UnitValue | None:
    return None if value is None else UnitValue(value=value, unit=UnitType.METERS)


def degrees(value: float | None) -> Angle | None:
    return None if value is None else Angle(value=value, unit=AngleUnit.DEGREES)


def polyline_sign_to_stroke(
    *,
    color: RGBColor | None = None,
    opacity: float | None = None,
    width: float | None = None,
    join: str | None = None,
    cap: str | None = None,
    dash_pattern: list[float] | None = None,
) -> Stroke:
    """Build a `Stroke` from a `PolylineSign` + its resolved `Style` (`LineStyle_Solid`/`LineStyle_Dashed`) and `Color`.

    `join`/`cap` pass through unchanged: `PolylineAttrs.Join`
    (`bevel`/`round`/`miter`) and `.Caps` (`round`/`butt`) already use the
    exact `stroke-linejoin`/`stroke-linecap` keywords pycartosym's SLD
    writer expects.

    KNOWN LOSSY: `Stroke.dash_pattern` is `list[int]`, matching the OGC
    CartoSym-JSON standard's own `dashPattern` definition exactly (verified
    against the standard's schema, not a pycartosym gap - it has no
    `unitValue`/`numericExpression` alternative there either, unlike
    `width`). `DashRec.DLength` values below 1.0 (real corpus,
    `RoadsExgm2ien_Symbols.xtf`'s `LineStyle_Dashed`: 0.1m dashes) round
    straight to 0 with a bare `round()`, collapsing the whole pattern to
    indistinguishable-from-solid - `round()` used as-is for now (no scaling
    applied), since SE 1.1.0's `stroke-dasharray` has no per-value unit
    either way (only a whole-symbolizer `uom`) so some precision loss is
    unavoidable at this layer regardless. Picking a target scale (e.g. mm)
    before rounding would reduce it - not done yet, backlog.
    """
    return Stroke(
        color=color,
        opacity=opacity,
        width=meters(width),
        join=join,
        cap=cap,
        dash_pattern=[round(d) for d in dash_pattern] if dash_pattern else None,
    )


def surface_sign_to_fill(
    *, fill_color: RGBColor | None = None, opacity: float | None = None, pattern: ImageGraphic | None = None
) -> Fill:
    """Build a `Fill` from `SurfaceSign.FillColor`/`HatchSymb` - `Clip`/`HatchOrg`/`HatchOffset` have no target."""
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

    `h_alignment`/`v_alignment` take INTERLIS's own `HALIGNMENT`/
    `VALIGNMENT` enum values, translated via `_H_ALIGNMENT`/`_V_ALIGNMENT`.
    `text`/`rotation` accept either a literal value or a CQL2 `PropertyRef`
    dict (`{"property": "..."}`, e.g. from `to_cql2` on a `DrawingRule`'s
    `Txt := Street -> Name`/`Rotation := NamOri` attribute-path assignment)
    - both are attribute-driven per real corpus usage, not fixed per style.
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

    `by_name` resolves the metaobject identity (`ili:Name`, a raw wire
    child every real SIGN BASKET data object carries even though it is
    never a declared `StandardSymbology` class attribute - confirmed
    against `Point_Graphics_Signatures.xtf`). `by_tid` resolves the
    `ili:ref` target of a REFERENCE-typed attribute (`Color`/`Symbol`/
    `FillColor`/...) to the object it points at, within the same basket.
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
        tid = _extract_reference(nodes[0])
        return self.by_tid.get(tid) if tid else None

    def resolve_color(self, obj: XtfObject, attr: str) -> tuple[RGBColor | None, float | None]:
        """Resolve a `Color`-typed reference attribute to `(RGBColor, opacity)` - `opacity` is `Color.T`."""
        nodes = obj.attributes.get(attr)
        return self._color_from_ref_node(nodes[0]) if nodes else (None, None)

    def _color_from_ref_node(self, ref_node: RawNode) -> tuple[RGBColor | None, float | None]:
        """Resolve a raw REFERENCE-attribute node (top-level or nested in a structure) to `(RGBColor, opacity)`."""
        tid = _extract_reference(ref_node)
        color_obj = self.by_tid.get(tid) if tid else None
        if color_obj is None:
            return None, None
        lum, c, h, t = (self.scalar(color_obj, name) for name in ("L", "C", "H", "T"))
        color = color_to_rgb(float(lum), float(c), float(h)) if lum and c and h else None
        return color, (float(t) if t else None)

    def dash_pattern(self, obj: XtfObject) -> list[float] | None:
        """Read a `LineStyle_Dashed.Dashes` (`LIST OF DashRec`) as a flat `DLength` list, in wire order.

        Each `LIST OF` occurrence is its own top-level `<Dashes>` sibling
        element (one `DashRec` inside), not one `<Dashes>` wrapper holding
        every `DashRec` as a child - confirmed against real refman data
        (`RoadsExgm2ien_Symbols.xtf`'s `LineStyle_Dashed`, 2 separate
        `<Dashes>` elements), same convention already used correctly by
        `font_symbol_geometry_to_circle_graphics` for `FontSymbol.Geometry`.
        Reading only the first occurrence's children (a hand-built
        synthetic fixture had assumed the single-wrapper shape) silently
        dropped every dash length after the first on real data.
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

    Only a single-boundary surface whose segments (after the mandatory
    StartSegment COORD) are all ARC segments carrying an explicit
    optional `<R>` (eCH-0031 SS4.3.11.14, the same element
    `xtf/validate.py::_validate_arc_node` already checks) is handled - a
    circle whose radius must instead be derived from 3 raw boundary
    points (no `<R>` on the wire) has no real corpus example and returns
    `None` here.
    """
    surface = _find_child(node, "SURFACE")
    boundary = _find_child(surface, "BOUNDARY") if surface is not None else None
    polyline = _find_child(boundary, "POLYLINE") if boundary is not None else None
    if polyline is None or len(polyline.children) < 2 or _geom_tag(polyline.children[0]) != "COORD":
        return None
    radii: list[float] = []
    for arc in polyline.children[1:]:
        if _geom_tag(arc) != "ARC":
            return None
        r_node = _find_child(arc, "R")
        if r_node is None or r_node.text is None:
            return None
        radii.append(float(r_node.text))
    return radii[0] if radii and all(abs(r - radii[0]) < 1e-9 for r in radii) else None


def _font_symbol_items(symbol_obj: XtfObject, structure_suffix: str) -> list[RawNode] | None:
    """Every `FontSymbol.Geometry` item's structure node, or `None` unless all of them are `structure_suffix`.

    A `FontSymbol` is "a collection of lines and surfaces" (its own model
    comment), so a composite of several stacked items is intended, not an
    edge case; every native-shape builder below is all-or-nothing on it -
    one foreign item aborts the whole symbol instead of silently dropping
    just that item, leaving the SVG fallback to render the mix.
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

    `None` for anything that is not one BOUNDARY of one all-COORD
    POLYLINE - an ARC segment, a hole, or a custom LINE FORM is not a
    polygon ring this way.
    """
    surface = _find_child(node, "SURFACE")
    boundaries = [c for c in surface.children if _geom_tag(c) in _BOUNDARY_TAGS] if surface is not None else []
    if len(boundaries) != 1:
        return None
    polyline = _find_child(boundaries[0], "POLYLINE")
    if polyline is None or not polyline.children:
        return None
    ring: list[tuple[float, float]] = []
    for segment in polyline.children:
        position = _read_coord(segment)
        if position is None:
            return None
        ring.append((position[0], position[1]))
    if len(ring) > 1 and math.dist(ring[0], ring[-1]) < _SHAPE_TOLERANCE:
        ring.pop()
    return ring or None


def _square_from_surface(node: RawNode) -> tuple[float, float] | None:
    """Read a `FontSymbol_Surface` boundary as `(side, orientation in degrees)` - a square, possibly rotated.

    Deliberately limited to the square case. A square's own 90-degree
    symmetry makes the rotation SIGN immaterial, so the emitted
    `Transform2D.orientation` is correct whichever way round CartoSym
    Part 2 means it (its `transform2D.orientation` carries no documented
    clockwise/counter-clockwise convention, and `se:Rotation` is
    clockwise while an INTERLIS symbol space is y-up); for an oblong
    rectangle the two conventions give visibly different results and
    nothing settles which is right, so those keep falling through to the
    SVG fallback. This is what renders a diamond: SE 1.1.0 has no
    `diamond` well-known mark name, and a diamond is a square turned 45
    degrees.
    """
    ring = _boundary_ring(node)
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


def font_symbol_geometry_to_rectangle_graphics(
    library: SignLibrary, symbol_obj: XtfObject, *, scale: float = 1.0
) -> list[RectangleGraphic] | None:
    """Build one `RectangleGraphic` per square `FontSymbol_Surface` item in a `Font.Type = symbol` `FontSymbol`.

    Gives a native `se:Mark`/`se:WellKnownName` square - and, rotated 45
    degrees, the diamond mark real Sachplan symbology uses - instead of
    the inline-SVG `ImageGraphic` the generic fallback would produce.
    See `_square_from_surface` for why only squares qualify.
    """
    structure_nodes = _font_symbol_items(symbol_obj, "FontSymbol_Surface")
    if structure_nodes is None:
        return None
    graphics: list[RectangleGraphic] = []
    for structure_node in structure_nodes:
        geometry_node = next((c for c in structure_node.children if c.tag == "Geometry"), None)
        square = _square_from_surface(geometry_node) if geometry_node is not None else None
        if square is None:
            return None
        side, orientation = square
        graphics.append(
            RectangleGraphic(
                type="Rectangle",
                width=meters(side * scale),
                height=meters(side * scale),
                fill=_surface_fill(library, structure_node),
                transform=Transform2D(orientation=orientation) if orientation > _SHAPE_TOLERANCE else None,
            )
        )
    return graphics or None


def font_symbol_geometry_to_circle_graphics(
    library: SignLibrary, symbol_obj: XtfObject, *, scale: float = 1.0
) -> list[CircleGraphic] | None:
    """Build one `CircleGraphic` per circular `FontSymbol_Surface` item in a `Font.Type = symbol` `FontSymbol`.

    Only the case where EVERY item is a `FontSymbol_Surface` whose
    boundary is a real circle (`_circle_radius_from_surface`) is handled
    - see `_font_symbol_items` for that all-or-nothing contract.
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

    `FontSymbol_Image` (`Uri: TEXT`) is a PROJECT EXTENSION of
    `FontSymbol.Geometry`'s `RESTRICTION`, not part of the official
    `StandardSymbology.ili` - proposed for "15-image-marker" and validated
    conditionally by the user pending `pycartosym` `ImageGraphic.hotSpot`
    support (confirmed working in v0.3.2 - see
    `tests/fixtures/cartosym/fontsymbol_image_repo/StandardSymbology_ext.ili`
    for the adapted model this was checked to actually build against).
    Only the case where EVERY item is a `FontSymbol_Image` is handled -
    same all-or-nothing contract as `font_symbol_geometry_to_circle_graphics`,
    a mix with `FontSymbol_Polyline`/`_Surface` aborts rather than
    guessing which one wins. No `Resource.type` (MIME type) is set - the
    official model proposal has no attribute to source it from, so
    `se:Format` is simply omitted rather than guessed from the URI's file
    extension. No `hot_spot` either, for the same reason (no anchor-point
    attribute proposed on `FontSymbol_Image`).
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
    """SVG elliptical-arc `A` command from `prev` to `end` through `mid` - all 3 points already in SVG (Y-down) space.

    Derives the circumcircle radius and the large-arc/sweep flags from
    the 3 points directly (INTERLIS's own 3-point arc encoding, eCH-0031
    V2.1.0 SS4.3.11.14) rather than trusting the optional `<R>` on the
    wire. `None` for 3 collinear/degenerate points - aborts the whole
    symbol rather than guessing a geometry (RULE #7 spirit).
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
    # In SVG's y-down space, an increasing atan2 angle sweeps visually
    # clockwise (sweep-flag 1) - r1 < r2 means the increasing (clockwise)
    # direction from `prev` reaches `mid` before `end`, so that's the
    # right direction; otherwise the decreasing (sweep-flag 0) direction
    # is, spanning (2*pi - r2) instead of r2.
    sweep, span = (1, r2) if r1 < r2 else (0, (2 * math.pi) - r2)
    large_arc = 1 if span > math.pi else 0
    return f"A {radius:.6g} {radius:.6g} 0 {large_arc} {sweep} {cx:.6g} {cy:.6g}"


def _svg_path_d(node: RawNode) -> str | None:
    """Build an SVG path `d` string from a POLYLINE SegmentSequence (COORD start, then COORD/ARC segments).

    Y is negated throughout - SVG's y-axis points down, an INTERLIS local
    symbol space is a standard y-up 2D plane (`FontSymbol`'s own comment:
    "defined for size 1.0 and scale 1.0" in user units). `_svg_arc_command`
    only ever sees already-flipped points, so its own flag derivation
    stays entirely within SVG's coordinate convention.
    """
    if _geom_tag(node) != "POLYLINE" or not node.children:
        return None
    segments = node.children
    if _geom_tag(segments[0]) != "COORD":
        return None
    start = _read_coord(segments[0])
    if start is None:
        return None
    current = (start[0], -start[1])
    parts = [f"M {current[0]:.6g} {current[1]:.6g}"]
    for seg in segments[1:]:
        tag = _geom_tag(seg)
        if tag == "COORD":
            pos = _read_coord(seg)
            if pos is None:
                return None
            current = (pos[0], -pos[1])
            parts.append(f"L {current[0]:.6g} {current[1]:.6g}")
        elif tag == "ARC":
            arc = _read_arc(seg)
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
    polyline = _find_child(node, "POLYLINE")
    if polyline is None:
        return None
    d = _svg_path_d(polyline)
    return f"{d} Z" if d is not None else None


def font_symbol_geometry_to_svg_data_uri(library: SignLibrary, symbol_obj: XtfObject) -> str | None:
    """Render a `FontSymbol`'s composite geometry as an inline `data:image/svg+xml` URI.

    Covers what `font_symbol_geometry_to_circle_graphics` doesn't (a
    `FontSymbol_Polyline` item, or a `FontSymbol_Surface` whose boundary
    isn't a plain circle) - real corpus data (`RoadsExgm2ien_Symbols.xtf`'s
    `FontSymbol ili:tid="101"/"102"`, "Triangle"/"NoParking") mixes both
    item kinds. `viewBox="-0.5 -0.5 1 1"` matches the model's own stated
    convention ("All font symbols are defined for size 1.0 and scale
    1.0") rather than a data-dependent bounding box, so every symbol
    renders at the same visual scale regardless of its own extent.
    `FontSymbol_Surface`'s 2nd+ boundary is a hole (`fill-rule="evenodd"`,
    same "first ring exterior, rest holes" convention as
    `convert/jsonfg.py::_read_surface`). `FontSymbol_Polyline.LineAttrs`
    (width/join/cap) is not read - a hairline default stroke is used, no
    real corpus example yet needs a specific width here.
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
            polyline_node = _find_child(geometry_node, "POLYLINE")
            d = _svg_path_d(polyline_node) if polyline_node is not None else None
            if d is None:
                return None
            color_node = next((c for c in structure_node.children if c.tag == "Color"), None)
            color, _opacity = library._color_from_ref_node(color_node) if color_node is not None else (None, None)
            layers.append(f'<path d="{d}" fill="none" stroke="{_rgb_to_hex(color)}"/>')
        elif structure_node.tag.endswith("FontSymbol_Surface"):
            surface_node = _find_child(geometry_node, "SURFACE")
            boundaries = [c for c in surface_node.children if _geom_tag(c) in _BOUNDARY_TAGS] if surface_node else []
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

    `Font.Type = text` (`Symbol` -> `FontSymbol` -> `Font`) builds a
    single glyph `TextGraphic`; `Font.Type = symbol` tries the native
    shape builders (circle, then square/diamond), then
    `font_symbol_geometry_to_image_graphics` (a project-extension
    `FontSymbol_Image`, see its own docstring), and finally an inline-SVG
    render of any remaining mix. Verified against real corpus data
    (`Point_Graphics_Signatures.xtf`'s `SymbolSign`/`FontSymbol`/`Font`)
    for the text case.
    """
    # `color` (from `SymbolSignColorAssoc`) has no confirmed pycartosym
    # target for a text-glyph Marker yet (neither `TextGraphic` nor `Font`
    # has a color field) - only `opacity` (`Color.T`) is applied here,
    # left as a follow-up rather than guessed.
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
                or font_symbol_geometry_to_rectangle_graphics(library, symbol_obj, scale=scale)
                or font_symbol_geometry_to_image_graphics(symbol_obj)
            )
            if elements is None:
                svg_uri = font_symbol_geometry_to_svg_data_uri(library, symbol_obj)
                if svg_uri is not None:
                    elements = [ImageGraphic(image=Resource(uri=svg_uri, type="image/svg+xml"))]
    return Marker(elements=elements, opacity=opacity)


def text_sign_object_to_font_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    """Build `text_sign_to_label` kwargs (`font_face`/`height`/`italic`/`color`/`opacity`) from a `TextSign` object.

    `Font` (`TextSignFontAssoc`) gives the face; `Height`/`Slanted` are
    `TextSign`'s own attributes directly (not references). `Color`
    (`TextSignColorAssoc`) resolves via the same `Color` wire mechanism as
    `SurfaceSign.FillColor`/`PolylineSign.Color`, now that `Font.color`/
    `opacity` exist in pycartosym (v0.3.0). `Underlined` is deliberately
    NOT read: pycartosym's SLD writer raises `NotImplementedError` for
    `Font.underline` unconditionally (confirmed reading its source) -
    wiring it would only ever produce a crash, not a silently-wrong value,
    but there is no real `TextSign` data to verify against either way.
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

    `Fill.pattern`/`Stroke.pattern` write correctly as of `pycartosym`
    v0.3.0 (`se:GraphicFill`/`se:GraphicStroke`, confirmed against the
    published wheel - a previous "blocked, NotImplementedError" note here
    was stale, tested against 0.2.2 and never rechecked after this
    project bumped past it) - but only `Dot`/`Circle`/`Rectangle`/`Image`
    graphic types are supported in that position (confirmed reading the
    writer source), never the free-form `ShapeGraphic` (`type: str`,
    e.g. a "vertline"/"cross" WKN) that would otherwise be the obvious
    match for a repeated hachure LINE. `Image` is the only viable target
    left - a single horizontal line across a `viewBox="-0.5 -0.5 1 1"`
    tile (same convention as `font_symbol_geometry_to_svg_data_uri`),
    tiled by `se:GraphicFill`. `HatchAng` (`SS_Angle`, default 0.0)
    rotates the whole tile via `Transform2D.orientation` - real corpus
    data (`Surface_Hatching_Stipples_GS.xtf`) never actually sets it, so
    the 0deg (untransformed) case is what's verified, not the rotated
    one. `HatchOffset`/`HatchOrg` have no target - `se:GraphicFill` has
    no separate pattern-spacing/anchor knob beyond the tile's own
    `se:Size`, so a value here couldn't be expressed without inventing a
    tiling convention `pycartosym`/SE doesn't have.
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
    """Build a `Fill` from `SurfaceSign.FillColor`/`HatchSymb` - `Clip`/`HatchOrg`/`HatchOffset` not resolved.

    Same `Color` wire mechanism as `symbol_sign_object_to_marker`
    (verified there against real data) applied to a different attribute
    name (`FillColor` vs `Color`) - verified end to end against real
    `SurfaceSign` data (`Surface_Hatching_Stipples_GS.xtf`).
    """
    color, opacity = library.resolve_color(obj, "FillColor")
    pattern = surface_sign_object_to_hatch_pattern(library, obj)
    return surface_sign_to_fill(fill_color=color, opacity=opacity, pattern=pattern)


def surface_sign_object_border_to_stroke(library: SignLibrary, obj: XtfObject) -> Stroke | None:
    """Build a `Stroke` from a `SurfaceSign` data object's `Border` (`SurfaceSignBorderAssoc`, a `PolylineSign` REF).

    Reuses `polyline_sign_object_to_stroke` on the referenced object - the
    border IS a real `PolylineSign` object, same wire shape as one
    referenced directly by a `PolylineSign` `Sign := {name}`.
    """
    border_obj = library.resolve_ref(obj, "Border")
    return polyline_sign_object_to_stroke(library, border_obj) if border_obj is not None else None


def polyline_sign_object_to_stroke(library: SignLibrary, obj: XtfObject) -> Stroke:
    """Build a `Stroke` from a real `PolylineSign` data object's `Color` and `Style` (width/join/cap/dash pattern).

    `Style` (`PolylineSignLineStyleAssoc`) resolves to a `LineStyle_Solid`/
    `_Dashed` object; its own `LineAttrs` association
    (`LineStyle_SolidPolylineAttrsAssoc`/`_DashedLineAttrsAssoc`) gives
    `Width`/`Join`/`Caps`, and a `LineStyle_Dashed` additionally gives its
    own `Dashes` (`LIST OF DashRec`) as the dash pattern. `PolylineSign.Color`
    (`PolylineSignColorAssoc`) and `LineStyle_Solid`/`_Dashed.Color`
    (`LineStyle_SolidColorAssoc`/`_DashedColorAssoc`) are BOTH `{0..1}` per
    `StandardSymbology.ili` - genuinely separate associations, not a
    redundant duplicate. Real corpus data (`RoadsExgm2ien_Symbols.xtf`'s
    `continuous`/`dotted` `PolylineSign`) sets only the `LineStyle`'s own
    `Color`, leaving `PolylineSign.Color` unset - `PolylineSign.Color`
    wins when both are set (no real corpus example exercises that case
    either way), the `LineStyle`'s is the fallback.
    """
    color, opacity = library.resolve_color(obj, "Color")
    width = join = cap = dashes = None
    style_obj = library.resolve_ref(obj, "Style")
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


def _symbol_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    return {"marker": symbol_sign_object_to_marker(library, obj)}


def _polyline_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    return {"stroke": polyline_sign_object_to_stroke(library, obj)}


def _surface_sign_kwargs(library: SignLibrary, obj: XtfObject) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"fill": surface_sign_object_to_fill(library, obj)}
    stroke = surface_sign_object_border_to_stroke(library, obj)
    if stroke is not None:
        kwargs["stroke"] = stroke
    return kwargs


def raster_sign_object_to_color_map(library: SignLibrary, obj: XtfObject) -> list[list[Any]] | None:
    """Build a `Symbolizer.color_map` from `RasterSign.ColorMapEntries` (a `LIST OF RasterColorMapEntry`).

    `RasterSign` is a PROJECT EXTENSION of `StandardSymbology` (a
    raster/coverage styling proposal covering only `ColorMap` so far, the
    one target confirmed to write a real `se:CoverageStyle/
    se:RasterSymbolizer/se:ColorMap` in `pycartosym`; channel selection
    and hill-shading are deliberately not built yet). `Symbolizer.color_map`
    is untyped (`Any`) - a plain list of `[value, color]` entries, in wire
    order, is what the writer actually expects (`_validated_map_pairs`,
    confirmed empirically against the real writer, NOT the separate
    standalone `ColorMap` Pydantic class which has an unrelated
    `{colors, values}` shape). `RasterColorMapEntry.Label` is deliberately
    NOT included here: `pycartosym`'s default `sld` dialect renders
    `se:Categorize`, which has no label/name slot at all in SE 1.1.0 (only
    `se:Threshold`/`se:Value`) - a 3-element `[value, color, label]` entry
    past the first used to crash its writer outright, now fixed upstream
    to a clean `NotImplementedError` instead (not yet released). Only the
    `sld:1.0.0`/`sld:geoserver` dialects (`se:ColorMapEntry`) support a
    label at all - staying on the default `sld` dialect and omitting
    `Label` is the choice made here, not a workaround for a bug anymore.
    Entirely synthetic (no real `.xtf` can exist yet for a class this
    project just proposed) - `tests/fixtures/cartosym/rastersign_repo/`.
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

    Mutually exclusive per `pycartosym`'s own writer constraint
    (`Symbolizer.colorChannels and Symbolizer.singleChannel cannot both
    be set`) - mirrored as a `MANDATORY CONSTRAINT` on the proposed
    `RasterSign` class. `{"property": <band name>}` is the only channel
    reference form the writer accepts (`_channel_source_name`) - band
    names are plain `TEXT` attributes, since INTERLIS carries no pixel
    data itself, only a reference to an external raster resource's bands.
    `GrayBand` wins if both happen to be set (the model's own constraint
    should already rule that out).
    """
    gray = library.scalar(obj, "GrayBand")
    if gray is not None:
        return {"single_channel": {"property": gray}}
    red, green, blue = (library.scalar(obj, name) for name in ("RedBand", "GreenBand", "BlueBand"))
    if red is not None and green is not None and blue is not None:
        return {"color_channels": [{"property": red}, {"property": green}, {"property": blue}]}
    return {}


def raster_sign_object_to_hill_shading(library: SignLibrary, obj: XtfObject) -> dict[str, Any] | None:
    """Build a `Symbolizer.hill_shading` dict from `RasterSign.HillShadeFactor` - the one non-blocked field.

    `hill_shading.sun`/`.colorMap`/`.opacityMap` are NOT proposed on
    `RasterSign` at all - confirmed blocked by SE 1.1.0's own Annex B
    (`_build_shaded_relief` raises for each, regardless of dialect), so
    there is nothing to gain from adding INTERLIS attributes for them.
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


def _with_feature_type(selector: dict[str, Any] | None, feature_type: str | None) -> dict[str, Any] | None:
    """Prepend a `dataLayer.id` conjunct - pycartosym's own writer pulls this back out as `se:FeatureTypeName`."""
    if feature_type is None:
        return selector
    conjunct = {"op": "=", "args": [{"sysId": "dataLayer.id"}, feature_type]}
    return conjunct if selector is None else {"op": "and", "args": [conjunct, selector]}


def styling_rule_from_drawing_rule(
    drawing_rule: MetaInstance, sign_library: SignLibrary | None = None, feature_type: str | None = None
) -> StylingRule:
    """Build one pycartosym `StylingRule` from a built INTERLIS `DrawingRule`.

    The `WHERE` selector compiles via `cql2.to_cql2` (the same CQL2-JSON
    shape `StylingRule.selector` expects natively). `feature_type`
    (typically the enclosing `Graphic.Base.Name` - `DrawingRule` has no
    back-reference to it, so the caller must pass it) is prepended as a
    `dataLayer.id` conjunct, pycartosym's own convention for `se:
    FeatureTypeName` (confirmed against its writer source). `Priority` ->
    `z_order`. A `Sign := {name}` reference resolves (via `sign_library`,
    a parsed SIGN BASKET data section - `None` skips it entirely, same as
    before this was wired) to its own library-object data, dispatched by
    `drawing_rule.Class` to `_symbol_sign_kwargs`/`_surface_sign_kwargs`/
    `_polyline_sign_kwargs` - those hold color/width/symbol/border; a
    `TextSign`'s `Txt`/`Rotation`/`HAli`/`VAli` are genuine `PARAMETER`s
    (`AbstractSymbology.Signs.TextSign`) set directly on the
    `DrawingRule`, unlike `Height`/`Font` which are OWN attributes only
    ever set via the referenced Sign object - resolved via
    `text_sign_object_to_font_kwargs` when `sign_object` is present (not
    itself corpus-verified, no real `TextSign` data object found).

    A `DrawingRule` with more than one `CondSignParamAssignment` (several
    independent `WHERE (...)` blocks under the same rule name, which would
    need `StylingRule.nested_rules`) has no real corpus example - only the
    first is used here (RULE #7).
    """
    conditions = drawing_rule.Rule if isinstance(drawing_rule.Rule, list) else [drawing_rule.Rule]
    cond = conditions[0]
    where = getattr(cond, "Where", None)
    selector = _with_feature_type(to_cql2(where) if where is not None else None, feature_type)

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
            text=params.get("Txt"),
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


def _priority_sort_key(rule: StylingRule) -> tuple[bool, float]:
    """Ascending `Priority` draws last/on top - this project's own convention, undocumented in `StandardSymbology.ili`.

    A rule whose `Priority` isn't a literal number (attribute-driven, no
    real corpus example) sorts after every literal one rather than being
    dropped or guessed at.
    """
    z_order = rule.symbolizer.z_order if rule.symbolizer else None
    return (not isinstance(z_order, (int, float)), z_order if isinstance(z_order, (int, float)) else 0.0)


def graphic_to_style(graphic: MetaInstance, sign_library: SignLibrary | None = None) -> Style:
    """Build one pycartosym `Style` from a built INTERLIS `GRAPHIC`, one `StylingRule` per `DrawingRule`.

    `feature_type` (`Graphic.Base.Name`) is prepended to every rule's
    selector (see `styling_rule_from_drawing_rule`). Rules are sorted by
    `Priority`, ascending (`_priority_sort_key`) - SLD/SE has no explicit
    z-order attribute, only document order, so this is where that ordering
    is actually realized.
    """
    feature_type = getattr(getattr(graphic, "Base", None), "Name", None)
    drawing_rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    styling_rules = [
        styling_rule_from_drawing_rule(rule, sign_library=sign_library, feature_type=feature_type)
        for rule in drawing_rules
    ]
    styling_rules.sort(key=_priority_sort_key)
    name = getattr(graphic, "Name", None)
    return Style(styling_rules=styling_rules, metadata=Metadata(title=name) if name else None)


def write_sld(style: Style) -> str:
    """Write `style` to SLD, working around pycartosym's SLD writer not yet supporting attribute-driven text rotation.

    `Transform2D.orientation` accepts a `PropertyRef` at the pycartosym
    model level, but the SLD writer's angle formatter raises
    `NotImplementedError` for anything but a literal number (confirmed
    empirically - real corpus data, e.g. `RoadsExgm2ien.ili`'s `Rotation
    := NamOri`, needs exactly the dynamic form). Reported to the
    pycartosym maintainer - remove this workaround once fixed there.
    Strips any `PropertyRef`-driven text rotation before handing `style`
    to the real writer, then patches the resulting XML to add it back as
    `se:Rotation><ogc:PropertyName>`, the same shape pycartosym already
    writes for other dynamic fields (`stroke-width`, `fill`, ...).
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
        # SE 1.1.0 PointPlacementType sequence: AnchorPoint?, Displacement?,
        # Rotation? - always appended last, after any AnchorPoint pycartosym
        # itself already wrote for HAli/VAli.
        rotation_el = etree.SubElement(point_placement, f"{{{_SE_NS}}}Rotation")
        etree.SubElement(rotation_el, f"{{{_OGC_NS}}}PropertyName").text = attr_name
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8").decode()
