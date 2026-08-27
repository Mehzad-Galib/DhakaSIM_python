"""Road-network geometry, shared by the live animation and the report.

The GUI is what the operator watches; the report's animation is what everyone
else sees. Those two pictures have to be the same picture, so the road surface
is built and painted in exactly one place -- here -- and both renderers call
:func:`paint`. Keeping one painter means the two cannot drift apart in shape any
more than they can in colour.

Everything in this module works in *pixel space*: metres multiplied by a
``pixel_per_meter`` the caller supplies. A renderer that wants a different
output size scales the result, rather than building the geometry at a different
size, so the shapes stay identical.
"""

from __future__ import annotations

import math

from .constants import Constants
from .javacompat import Color
from .parameters import Parameters
from . import utilities as Utilities

#: Kerb outline width, in metres. At the GUI's 15 px/m this is the 15-pixel
#: stroke the road outlines have always used.
#: Painted width of the carriageway edge, in metres.  Thin on purpose: a wide
#: dark band swamps the lane dividers, and the edge should read as a line at
#: the side of the road rather than as an outline drawn around it.
KERB_WIDTH_METRES = 0.25

#: Kerb width used when drawing over map imagery.  Wider than the real thing,
#: because with no road fill the outline is carrying the whole shape of the
#: carriageway, and a quarter-metre line is a single pixel at ordinary zoom.
OVERLAY_KERB_WIDTH_METRES = 0.6

#: Nominal painted lane width, in metres.  Only the markings use it: the
#: simulation itself has no lanes, and a vehicle straddles as many strips as it
#: likes.  Drivers ignoring the paint is the behaviour being modelled, so the
#: dividers are drawn at a realistic road-marking spacing rather than at the
#: strip boundaries, which at 0.5 m would be hatching, not lane lines.
LANE_WIDTH_METRES = 3.5

#: Dash and gap of a divider line, in metres.
MARKING_DASH_METRES = 3.0
MARKING_GAP_METRES = 3.0

#: Painted width of a divider, in metres.
MARKING_WIDTH_METRES = 0.15

#: How far a mitred kerb corner may reach out from a bend before the corner is
#: cut off square instead, as a multiple of the carriageway width.  A mitre
#: grows without bound as a bend approaches a hairpin, and a spike sticking out
#: of the road is far more conspicuous than the notch it was put there to hide.
MITRE_LIMIT = 2.5


#: How much of the narrowest arm's width becomes the junction corner
#: radius.  Around one carriageway width matches the kerb radii on
#: Dhaka's larger junctions; smaller values sharpen the corners.
CORNER_RADIUS_FACTOR = 1.3

#: How far past a boundary node the carriageway is drawn, in metres.  The
#: road does not end where the network does, and a vehicle entering or
#: leaving at the boundary is simulated at up to a body length beyond the
#: link's end -- a bus is about twelve metres -- so without this stub it is
#: drawn standing on the background imagery.  Drawing only: the model's
#: links, sensors and strips are untouched.
BOUNDARY_STUB_METRES = 12.0

#: Control-point reach of a turn connector, as a fraction of the straight-line
#: distance between the two arm mouths.  0.39 reproduces a true quarter circle
#: for a right-angle turn; a little more makes the turn swing wider, which is
#: how a real vehicle takes it.
CONNECTOR_TENSION = 0.42

#: How many points each connector curve is sampled at.  The ribbon is filled as
#: a polygon, so this is the only thing separating a smooth turn from a faceted
#: one.
CONNECTOR_STEPS = 14


def node_point(link_list, node):
    """Where a node actually sits, in metres.

    Boundary nodes carry real coordinates; junction nodes are stored at
    ``(0, 0)``, so their position is recovered as the mean of the link endpoints
    that meet there.
    """
    n = node.number_of_links()
    if n == 0:
        return node.x, node.y
    sx = sy = 0.0
    for j in range(n):
        link = link_list[node.get_link(j)]
        if link.get_up_node() == node.get_id():
            seg = link.get_first_segment()
            sx += seg.get_start_x()
            sy += seg.get_start_y()
        else:
            seg = link.get_last_segment()
            sx += seg.get_end_x()
            sy += seg.get_end_y()
    return sx / n, sy / n


#: Fewest points a circle is ever drawn with, and the longest a facet of one
#: may be in pixels.  A circle is filled as a polygon and its kerb is stroked
#: edge by edge, so these are all that separate a circle from a polygon on
#: screen.  The cap keeps a large roundabout from costing thousands of points.
CIRCLE_STEPS = 48
CIRCLE_FACET_PIXELS = 2.0
CIRCLE_MAX_STEPS = 240

#: A roundabout's paved area is not the ring: it is the ring plus every arm's
#: throat, and the two are drawn as one outline.  These control it.
#:
#: `ROUNDABOUT_REACH` is how far out of the ring that outline may go, as a
#: multiple of the outer radius.  Beyond it the arm's own ribbon takes over,
#: and since the outline is inside the ribbon there the join is invisible.
#:
#: `ROUNDABOUT_CORNER_DEGREES` is the angular width of the largest notch the
#: outline will round off.  Between two neighbouring arms is a real kerb
#: island and it should stay; between an arm and the ring is a sharp V that
#: exists only because a mouth is a straight cut across a curved junction, and
#: it should not.  Seven degrees is about fifteen metres at Khamarbari's ring:
#: wide enough for the V, too narrow to swallow an island.
#:
#: `ROUNDABOUT_MIN_STEPS` is the coarsest the outline is ever sampled at.  The
#: shape carries real detail -- six mouths and six islands -- so it needs more
#: points than a plain circle even when it is small on screen.
ROUNDABOUT_REACH = 1.9
ROUNDABOUT_CORNER_DEGREES = 7.0
ROUNDABOUT_MIN_STEPS = 180

#: What counts as a corner rather than a kerb turning, as a fraction of the
#: outer radius: a radius that changes by more than this between two samples
#: is a step in the outline, not a slope.  A kerb line at the reach limit
#: swings a couple of metres a sample; the outer end of a mouth drops ten or
#: more.
ROUNDABOUT_CORNER_JUMP = 0.2



def circle_steps(radius):
    """Enough points that the circle's facets are shorter than the eye's."""
    if radius <= 0:
        return CIRCLE_STEPS
    wanted = int(2.0 * math.pi * radius / CIRCLE_FACET_PIXELS)
    return max(CIRCLE_STEPS, min(wanted, CIRCLE_MAX_STEPS))


def circle_polygon(cx, cy, radius, steps=None):
    """A circle as a polygon, wound the same way a convex hull is."""
    if steps is None:
        steps = circle_steps(radius)
    return [(cx + radius * math.cos(2.0 * math.pi * i / steps),
             cy + radius * math.sin(2.0 * math.pi * i / steps))
            for i in range(steps)]


def roundabout_centre(link_list, node):
    """A roundabout's centre, in metres.

    Not the mean of the arms' endpoints, which is what :func:`node_point`
    gives.  Once the arms have been pulled back to the edge of the circle
    their endpoints lie *on* that circle, and the mean of points on a circle
    is only its centre when they are evenly spread -- which arms never are.
    The processor records the true centre on the node before it moves
    anything, so use that when it is there.
    """
    getter = getattr(node, "get_centre", None)
    if getter is not None and node.is_roundabout():
        # Recorded by the processor before it moved a single arm, so it is
        # true whatever the arms have been cut back to since.  Asked only of a
        # roundabout: any other node keeps (0, 0) there and means nothing by it.
        return getter()
    return node_point(link_list, node)


def convex_hull(pts):
    """Monotone-chain convex hull of *pts*."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def fillet_polygon(pts, radius, segments=7):
    """Round every corner of a convex polygon with a circular arc.

    A junction built as a bare convex hull meets its arms in straight
    chamfers, which is what makes the intersection read as abrupt next to a
    real one, where the kerb turns through a radius. Each vertex is cut back
    along both of its edges and the gap bridged with an arc.

    The cut-back distance is clamped to half of the shorter adjacent edge, so
    neighbouring fillets can never overrun each other and swallow an edge
    whole; the radius is then recomputed from whatever cut survived, which is
    why a tight corner rounds less than a wide one instead of collapsing.
    """
    n = len(pts)
    if n < 3 or radius <= 0:
        return list(pts)

    out = []
    for i in range(n):
        ax, ay = pts[(i - 1) % n]
        bx, by = pts[i]
        cx, cy = pts[(i + 1) % n]

        v1x, v1y = ax - bx, ay - by
        v2x, v2y = cx - bx, cy - by
        l1 = math.hypot(v1x, v1y)
        l2 = math.hypot(v2x, v2y)
        if l1 <= 1e-9 or l2 <= 1e-9:
            out.append((bx, by))
            continue
        v1x, v1y = v1x / l1, v1y / l1
        v2x, v2y = v2x / l2, v2y / l2

        # Interior angle at B, via the dot product of the two edge directions.
        cosine = max(-1.0, min(1.0, v1x * v2x + v1y * v2y))
        theta = math.acos(cosine)
        # Nearly straight or folded back on itself: nothing to round.
        if theta < 1e-3 or theta > math.pi - 1e-3:
            out.append((bx, by))
            continue

        half = theta / 2.0
        cut = min(radius / math.tan(half), l1 / 2.0, l2 / 2.0)
        if cut <= 1e-9:
            out.append((bx, by))
            continue
        r = cut * math.tan(half)

        p1 = (bx + v1x * cut, by + v1y * cut)
        p2 = (bx + v2x * cut, by + v2y * cut)

        # Centre lies along the angle bisector, r / sin(half) from the vertex.
        bisx, bisy = v1x + v2x, v1y + v2y
        blen = math.hypot(bisx, bisy)
        if blen <= 1e-9:
            out.append((bx, by))
            continue
        dist = r / math.sin(half)
        ox, oy = bx + bisx / blen * dist, by + bisy / blen * dist

        a1 = math.atan2(p1[1] - oy, p1[0] - ox)
        a2 = math.atan2(p2[1] - oy, p2[0] - ox)
        # Sweep the short way round, whichever direction the hull winds in.
        sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        for k in range(segments + 1):
            ang = a1 + sweep * k / segments
            out.append((ox + r * math.cos(ang), oy + r * math.sin(ang)))
    return out


def link_end_at_node(link, node, pixel_per_meter):
    """The two outer corner points of *link* where it meets *node*."""
    if link.get_up_node() == node.get_id():
        segment = link.get_first_segment()
        x1 = segment.get_start_x() * pixel_per_meter
        y1 = segment.get_start_y() * pixel_per_meter
        x2 = segment.get_end_x() * pixel_per_meter
        y2 = segment.get_end_y() * pixel_per_meter
        w = segment.get_segment_width() * pixel_per_meter
        return (x1, y1,
                Utilities.return_x3(x1, y1, x2, y2, w),
                Utilities.return_y3(x1, y1, x2, y2, w))
    segment = link.get_last_segment()
    x1 = segment.get_start_x() * pixel_per_meter
    y1 = segment.get_start_y() * pixel_per_meter
    x2 = segment.get_end_x() * pixel_per_meter
    y2 = segment.get_end_y() * pixel_per_meter
    w = segment.get_segment_width() * pixel_per_meter
    return (x2, y2,
            Utilities.return_x4(x1, y1, x2, y2, w),
            Utilities.return_y4(x1, y1, x2, y2, w))


def segment_quads(link_list, pixel_per_meter):
    """The four corner points of every road segment, in pixel space."""
    quads = []
    for link in link_list:
        for j in range(link.get_number_of_segments()):
            seg = link.get_segment(j)
            x1 = seg.get_start_x() * pixel_per_meter
            y1 = seg.get_start_y() * pixel_per_meter
            x2 = seg.get_end_x() * pixel_per_meter
            y2 = seg.get_end_y() * pixel_per_meter
            w = seg.get_segment_width() * pixel_per_meter
            # A zero-length segment has no perpendicular: return_x3 answers
            # NaN (Java semantics), the quad would paint nowhere and Tk
            # refuses NaN coordinates outright.  Nothing to draw or probe,
            # so skip it, as carriageways does.
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9 or w <= 0.0:
                continue
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            x4 = Utilities.return_x4(x1, y1, x2, y2, w)
            y4 = Utilities.return_y4(x1, y1, x2, y2, w)
            quads.append(([x1, x2, x4, x3], [y1, y2, y4, y3]))
    return quads


def _line_intersection(px, py, dx, dy, qx, qy, ex, ey):
    """Where the two lines cross, or ``None`` when they are parallel."""
    den = dx * ey - dy * ex
    if abs(den) < 1e-12:
        return None
    t = ((qx - px) * ey - (qy - py) * ex) / den
    return (px + dx * t, py + dy * t)


def _mitre(px, py, da, na, wa, db, nb, wb):
    """The far edge's corner at one interior vertex, as one point or two.

    Both offset lines are followed to where they meet, which is exact even
    when the two segments are different widths.  A bend approaching a hairpin
    sends that meeting point off towards infinity, so past ``MITRE_LIMIT`` the
    corner is cut square and the chord between the two offset corners closes
    the ribbon instead.
    """
    ax, ay = px + na[0] * wa, py + na[1] * wa
    bx, by = px + nb[0] * wb, py + nb[1] * wb
    hit = _line_intersection(ax, ay, da[0], da[1], bx, by, db[0], db[1])
    if hit is not None:
        if math.hypot(hit[0] - px, hit[1] - py) <= MITRE_LIMIT * max(wa, wb):
            return [hit]
    return [(ax, ay), (bx, by)]


def _ribbon(near, dirs, normals, widths):
    """One chain of segments as a pair of kerb lines running the same way."""
    far = []
    last = len(near) - 1
    for i in range(len(near)):
        px, py = near[i]
        if i == 0 or i == last:
            nx, ny = normals[0] if i == 0 else normals[-1]
            w = widths[0] if i == 0 else widths[-1]
            far.append((px + nx * w, py + ny * w))
        else:
            far.extend(_mitre(px, py,
                              dirs[i - 1], normals[i - 1], widths[i - 1],
                              dirs[i], normals[i], widths[i]))
    return (list(near), far)


#: How far back from a drawn bend the corner-cutting may reach, in metres.
#: Drawing only -- the model's segments are untouched, so the cap is what
#: keeps a vehicle on its own straight segment from visibly leaving the
#: relaxed ribbon: the drawn line moves at most a couple of metres inside a
#: bend, well under half a carriageway.
SMOOTH_CUT_METRES = 8.0
#: Bends flatter than this stay uncut; cutting them buys nothing and doubles
#: the ribbon's point count.
SMOOTH_MIN_DEGREES = 4.0

#: How far behind the painted junction outline a stop line (and its signal
#: bar) stands, in metres.  A single metre put the first vehicle's nose on
#: the paint's very edge, which read as queuing inside the box; a car
#: length's clearance reads as a real stop line.
STOP_LINE_CLEARANCE_METRES = 6.0


def _smooth_chain(pts, widths, cap):
    """One corner-cutting pass over a drawn polyline; ``widths`` per edge.

    Each interior vertex is replaced by two points ``cap``-or-less back along
    its edges (never more than a third of either edge, so neighbouring
    corners cannot overlap).  Endpoints never move: everything anchored to a
    link end -- junction mouths, stop lines, signal bars -- stays exactly
    where the model puts it, and only the line between them relaxes.
    """
    if len(pts) < 3:
        return pts, widths
    min_cos = math.cos(math.radians(SMOOTH_MIN_DEGREES))
    new_pts = [pts[0]]
    new_ws = []
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        vx, vy = pts[i]
        bx, by = pts[i + 1]
        len_prev = math.hypot(vx - ax, vy - ay)
        len_next = math.hypot(bx - vx, by - vy)
        if len_prev <= 1e-9 or len_next <= 1e-9:
            new_pts.append((vx, vy))
            new_ws.append(widths[i - 1])
            continue
        ux1, uy1 = (vx - ax) / len_prev, (vy - ay) / len_prev
        ux2, uy2 = (bx - vx) / len_next, (by - vy) / len_next
        if ux1 * ux2 + uy1 * uy2 >= min_cos:
            new_pts.append((vx, vy))          # already straight enough
            new_ws.append(widths[i - 1])
            continue
        d = min(cap, len_prev / 3.0, len_next / 3.0)
        new_pts.append((vx - ux1 * d, vy - uy1 * d))
        new_ws.append(widths[i - 1])
        new_pts.append((vx + ux2 * d, vy + uy2 * d))
        new_ws.append(widths[i - 1])
    new_pts.append(pts[-1])
    new_ws.append(widths[-1])
    return new_pts, new_ws


def _smooth_segs(segs, pixel_per_meter):
    """Round the interior bends of a link's drawn segment chain.

    Takes and returns the ``[x1, y1, x2, y2, w, length]`` rows
    :func:`carriageways` builds, so the ribbon loop downstream needs no
    changes.  Chain breaks (a gap between one segment's end and the next's
    start) are respected: each continuous run is smoothed on its own.
    """
    out = []
    group = []

    def flush():
        if not group:
            return
        pts = [(group[0][0], group[0][1])]
        widths = []
        for x1, y1, x2, y2, w, _length in group:
            pts.append((x2, y2))
            widths.append(w)
        # Two passes, the second with half the reach: one pass halves a kink,
        # two read as a curve, and the shrinking cap keeps the second pass
        # from re-cutting what the first already rounded.
        for cap_m in (SMOOTH_CUT_METRES, SMOOTH_CUT_METRES / 2.0):
            pts, widths = _smooth_chain(pts, widths, cap_m * pixel_per_meter)
        for (ax, ay), (bx, by), w in zip(pts, pts[1:], widths):
            length = math.hypot(bx - ax, by - ay)
            if length > 1e-9:
                out.append([ax, ay, bx, by, w, length])
        group.clear()

    for row in segs:
        if group and math.hypot(row[0] - group[-1][2],
                                row[1] - group[-1][3]) > 1e-6:
            flush()
        group.append(row)
    flush()
    return out


def carriageways(link_list, pixel_per_meter, stub_nodes=None):
    """Every link as one continuous ribbon, in pixel space.

    *stub_nodes* are the boundary node ids; a link end that terminates on one
    is drawn ``BOUNDARY_STUB_METRES`` longer, so a vehicle crossing the
    network edge stays on painted road.

    Returns ``(near, far)`` per link -- the two kerb lines as point lists,
    both running from the link's start to its end -- so the surface to fill is
    ``near + reversed(far)`` and the kerbs are those same two polylines.

    :func:`segment_quads` already gives a quad per segment, and filling those
    was how the road used to be painted.  It cannot work on a link that bends.
    A link is a chain and only one of its two edges is shared: consecutive
    segments meet exactly at their stated ends, but the far edge is offset
    along each segment's *own* normal, so at a bend of theta the two far
    corners stand ``2 w sin(theta / 2)`` apart -- five metres on a fourteen
    metre road turning twenty degrees.  The networks fitted to real roads bend
    by about that much at every joint, so the carriageway came out as a row of
    planks with a wedge missing between each pair.  Offsetting the chain as a
    whole and mitring the interior vertices closes the wedge and lets the kerb
    run through unbroken.
    """
    out = []
    stub_nodes = frozenset() if stub_nodes is None else stub_nodes
    for link in link_list:
        segs = []
        for j in range(link.get_number_of_segments()):
            seg = link.get_segment(j)
            x1 = seg.get_start_x() * pixel_per_meter
            y1 = seg.get_start_y() * pixel_per_meter
            x2 = seg.get_end_x() * pixel_per_meter
            y2 = seg.get_end_y() * pixel_per_meter
            w = seg.get_segment_width() * pixel_per_meter
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= 1e-9 or w <= 0.0:
                continue
            segs.append([x1, y1, x2, y2, w, length])
        if not segs:
            continue
        # A boundary end gets a stub: extend the terminal segment along its
        # own direction, so the extension is collinear and adds no mitre.
        # Segments run up -> down, so the first one starts at the up node.
        reach = BOUNDARY_STUB_METRES * pixel_per_meter
        if link.get_up_node() in stub_nodes:
            x1, y1, x2, y2, w, length = segs[0]
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            segs[0] = [x1 - ux * reach, y1 - uy * reach, x2, y2, w,
                       length + reach]
        if link.get_down_node() in stub_nodes:
            x1, y1, x2, y2, w, length = segs[-1]
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            segs[-1] = [x1, y1, x2 + ux * reach, y2 + uy * reach, w,
                        length + reach]
        # Drawing only: relax the bends before the ribbon is offset, so the
        # kerbs run as curves instead of a chain of kinks.  The model's
        # segments -- what vehicles actually drive -- are untouched.
        segs = _smooth_segs(segs, pixel_per_meter)
        near, dirs, normals, widths = [], [], [], []
        for x1, y1, x2, y2, w, length in segs:
            # return_x3 divides by the segment's length on the way to its
            # perpendicular, so a zero-length segment was dropped above
            # rather than asked for kerb corners.
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            if near and math.hypot(x1 - near[-1][0], y1 - near[-1][1]) > 1e-6:
                # A break in the chain.  Nothing joins what came before to
                # what comes next, so it is finished off as a ribbon of its
                # own rather than closed across the gap.
                if len(near) >= 2:
                    out.append(_ribbon(near, dirs, normals, widths))
                near, dirs, normals, widths = [], [], [], []
            if not near:
                near.append((x1, y1))
            near.append((x2, y2))
            dirs.append(((x2 - x1) / length, (y2 - y1) / length))
            normals.append(((x3 - x1) / w, (y3 - y1) / w))
            widths.append(w)
        if len(near) >= 2:
            out.append(_ribbon(near, dirs, normals, widths))
    return out


def lane_markings(link_list, pixel_per_meter):
    """Dashed lane dividers along every segment, in pixel space.

    Returns a flat list of dash segments, so a surface only has to draw lines.
    The outermost boundaries are left out: those are the kerbs, and the road
    already has an edge drawn there.

    A segment carrying one lane's worth of width gets no divider at all, which
    is why the rickshaw lanes of a narrow Dhaka side street stay unmarked.
    """
    dashes = []
    step = MARKING_DASH_METRES + MARKING_GAP_METRES
    for link in link_list:
        # The same chain the carriageway ribbon is built from, smoothed the
        # same way, or the dividers would run straight across the very bends
        # the ribbon now rounds and poke out of the kerb.
        segs = []
        for j in range(link.get_number_of_segments()):
            seg = link.get_segment(j)
            x1 = seg.get_start_x() * pixel_per_meter
            y1 = seg.get_start_y() * pixel_per_meter
            x2 = seg.get_end_x() * pixel_per_meter
            y2 = seg.get_end_y() * pixel_per_meter
            w = seg.get_segment_width() * pixel_per_meter
            length = math.hypot(x2 - x1, y2 - y1)
            # Tested before the perpendicular is asked for: a zero-length
            # segment divides by zero inside Utilities.return_x3.
            if length <= 1e-9 or w <= 0.0:
                continue
            segs.append([x1, y1, x2, y2, w, length])
        for x1, y1, x2, y2, w, _length in _smooth_segs(segs, pixel_per_meter):
            lanes = int(round(w / pixel_per_meter / LANE_WIDTH_METRES))
            if lanes < 2:
                continue

            # the far kerb, as segment_quads builds it
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            x4 = Utilities.return_x4(x1, y1, x2, y2, w)
            y4 = Utilities.return_y4(x1, y1, x2, y2, w)

            dash_px = MARKING_DASH_METRES * pixel_per_meter
            step_px = step * pixel_per_meter
            if step_px <= 1e-9:
                continue

            for lane in range(1, lanes):
                t = lane / lanes
                # the divider's two ends, interpolated across the carriageway
                ax = x1 + (x3 - x1) * t
                ay = y1 + (y3 - y1) * t
                bx = x2 + (x4 - x2) * t
                by = y2 + (y4 - y2) * t
                run = math.hypot(bx - ax, by - ay)
                if run <= 1e-9:
                    continue
                ux, uy = (bx - ax) / run, (by - ay) / run
                travelled = 0.0
                while travelled < run:
                    end = min(travelled + dash_px, run)
                    dashes.append((ax + ux * travelled, ay + uy * travelled,
                                   ax + ux * end, ay + uy * end))
                    travelled += step_px
    return dashes


def point_in_polygon(px, py, xs, ys):
    """Ray-casting test. The polygons here are all convex, but the arcs a
    fillet leaves behind make a dedicated convex test more trouble than this."""
    inside = False
    n = len(xs)
    j = n - 1
    for i in range(n):
        if (ys[i] > py) != (ys[j] > py):
            t = (xs[j] - xs[i]) * (py - ys[i]) / (ys[j] - ys[i])
            if px < xs[i] + t:
                inside = not inside
        j = i
    return inside


def link_label_offset(link, geometry, pixel_per_meter, half_text_m,
                      text_up_m=0.0):
    """World (metre) point at which to draw a link's street-name label.

    A name laid on the carriageway midpoint sits under the traffic, so the
    label is pushed off the road: perpendicular to the link's middle
    segment, at the nearest spot where the text lands on no drawn road.

    The two perpendicular sides are nothing like symmetric -- ``link.txt``
    states one kerb edge with the carriageway hanging a full width off one
    side, and over imagery the drawn road is wider again -- so rather than
    reason about which side is which, both are probed against the built
    geometry (segment quads and junction patches, the actual paint) at
    increasing clearances, and the first clear spot wins.  The reach of the
    text to either side (``half_text_m``, horizontal, since labels are drawn
    upright) must be clear too, or a name beside one road overhangs the
    next -- and so must the row one text height up (``text_up_m``), because
    the anchor is the *baseline*: a label placed just below a road would
    otherwise poke its glyphs back onto it.  Falls back to the on-road
    midpoint if nothing within reach is clear, which is the old behaviour
    and still legible under the halo.
    """
    count = link.get_number_of_segments()
    seg = link.get_segment(count // 2)
    x_m = (seg.get_start_x() + seg.get_end_x()) / 2.0
    y_m = (seg.get_start_y() + seg.get_end_y()) / 2.0
    if geometry is None:
        return x_m, y_m
    dx = seg.get_end_x() - seg.get_start_x()
    dy = seg.get_end_y() - seg.get_start_y()
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return x_m, y_m
    dx, dy = dx / length, dy / length
    quads, hulls = geometry[0], geometry[1]

    def clear(qx_m, qy_m):
        px, py = qx_m * pixel_per_meter, qy_m * pixel_per_meter
        for xs, ys in quads:
            if point_in_polygon(px, py, xs, ys):
                return False
        for hxs, hys, _cx, _cy, _radius, _kerb in hulls:
            if point_in_polygon(px, py, hxs, hys):
                return False
        return True

    # Walk out to the full stated width plus the overlay widening and a
    # margin: from the kerb edge, one side clears in a step or two and the
    # other must cross the whole carriageway first.
    limit = seg.get_segment_width() + Constants.OVERLAY_WIDEN_METRES + 10.0
    step = 2.0
    d = step
    while d <= limit:
        for nx, ny in ((-dy, dx), (dy, -dx)):
            px_m, py_m = x_m + nx * d, y_m + ny * d
            if all(clear(px_m + off, py_m - up)
                   for off in (-half_text_m, 0.0, half_text_m)
                   for up in (0.0, text_up_m)):
                return px_m, py_m
        d += step
    return x_m, y_m


def junction_kerb(hull, cx, cy, quads, probe):
    """The stretches of a junction outline that should be drawn as kerb.

    A junction patch is bounded partly by real kerb, which a driver sees as the
    edge of the road, and partly by the open mouths where each arm carries on.
    Stroking the whole outline would rule a line straight across every
    approach; stroking none of it leaves the arms' own kerbs ending in mid-air,
    which is what makes an intersection read as unfinished.

    An edge is told apart by stepping a little way outwards from it at its
    midpoint: land on road surface and the edge is a mouth, land on nothing
    and it is kerb.

    Outwards means along the edge's own normal, taking whichever of the two
    points away from the patch centre -- not along the radius from that
    centre.  On a convex hull the two agree closely, because its edges run
    across the arms rather than along them.  A roundabout's outline does both:
    where it follows an arm's kerb it runs almost straight out from the
    centre, so a radial probe slides *along* the kerb instead of off it and
    the answer comes back as a coin toss.
    """
    edges = []
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        ex, ey = x2 - x1, y2 - y1
        span = math.hypot(ex, ey)
        if span <= 1e-9:
            continue
        vx, vy = ey / span, -ex / span
        if vx * (mx - cx) + vy * (my - cy) < 0.0:
            vx, vy = -vx, -vy
        length = math.hypot(vx, vy)
        if length <= 1e-9:
            continue
        ox = mx + vx / length * probe
        oy = my + vy / length * probe
        if any(point_in_polygon(ox, oy, qxs, qys) for qxs, qys in quads):
            continue                      # the road carries on here
        edges.append((x1, y1, x2, y2))
    return edges


def junction_hulls(node_list, link_list, pixel_per_meter, quads=None):
    """A filled convex patch for every junction (node with >1 link).

    The patch is built from each incident link's two kerb corners at the node
    *and* the same corners carried a short way along the link, so the patch
    overlaps the road surfaces and leaves no notch at the mouth.
    """
    if quads is None:
        quads = segment_quads(link_list, pixel_per_meter)
    # Half a metre out from the edge: far enough to be clearly on the road
    # beyond a mouth, near enough not to jump a narrow verge.  Deliberately
    # not tied to the kerb width, which is a drawing thickness.
    probe = max(2.0, 0.5 * pixel_per_meter)
    hulls = []
    for node in node_list:
        if node.number_of_links() < 2:
            continue
        if node.is_roundabout():
            # A roundabout is not a junction patch with an ornament in the
            # middle, and it is not a bare circle either: it is the ring and
            # every arm's throat as one shape.  See roundabout_outline.  The
            # convex hull an ordinary junction uses is no good here -- it
            # bridges straight between neighbouring arms and paves over the
            # ring, leaving an island floating in the middle of a blob.
            cx, cy = roundabout_centre(link_list, node)
            cx *= pixel_per_meter
            cy *= pixel_per_meter
            hull = roundabout_outline(node, link_list, pixel_per_meter)
            if len(hull) < 3:
                continue
            # radius 0: the disc in a hull tuple is there to round off a
            # concave mouth a convex hull cannot reach, and this outline
            # follows the arms already.  Filling it as well would only lay a
            # second fill over the first.
            hulls.append(([p[0] for p in hull], [p[1] for p in hull],
                          cx, cy, 0.0,
                          junction_kerb(hull, cx, cy, quads, probe)))
            continue
        hull, widths = junction_outline(node, link_list, pixel_per_meter)
        if hull is not None:
            # A disc at the junction centre rounds off any concave mouth the
            # convex hull cannot reach.
            cx = sum(p[0] for p in hull) / len(hull)
            cy = sum(p[1] for p in hull) / len(hull)
            radius = max(widths) * pixel_per_meter * 0.5
            hulls.append(([p[0] for p in hull], [p[1] for p in hull],
                          cx, cy, radius,
                          junction_kerb(hull, cx, cy, quads, probe)))
    return hulls


def junction_outline(node, link_list, pixel_per_meter):
    """The filleted patch outline drawn for an ordinary junction.

    Returns ``(hull points, arm widths)``, or ``(None, widths)`` when the
    node's geometry degenerates.  Split out of :func:`junction_hulls` so the
    stop lines can be read off the very same outline the patch is painted
    from (:func:`stop_line_setbacks`), rather than off an approximation that
    drifts from the picture.
    """
    pts = []
    widths = []
    for j in range(node.number_of_links()):
        link = link_list[node.get_link(j)]
        if link.get_up_node() == node.get_id():
            seg = link.get_first_segment()
            ax, ay = seg.get_start_x(), seg.get_start_y()
            bx, by = seg.get_end_x(), seg.get_end_y()
        else:
            seg = link.get_last_segment()
            ax, ay = seg.get_end_x(), seg.get_end_y()
            bx, by = seg.get_start_x(), seg.get_start_y()
        widths.append(seg.get_segment_width())
        x1, y1, x3, y3 = link_end_at_node(link, node, pixel_per_meter)
        pts.append((x1, y1))
        pts.append((x3, y3))
        # unit vector pointing from the node into the link
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length > 0:
            reach = seg.get_segment_width() * pixel_per_meter
            ux, uy = dx / length * reach, dy / length * reach
            pts.append((x1 + ux, y1 + uy))
            pts.append((x3 + ux, y3 + uy))
    hull = convex_hull(pts)
    if len(hull) < 3:
        return None, widths
    # Round the corners before filling.  The kerb of a real junction turns
    # through a radius rather than a straight chamfer, and the narrowest arm
    # sets it: a side road cannot carry a wider corner than its own
    # carriageway.
    corner = min(widths) * pixel_per_meter * CORNER_RADIUS_FACTOR
    return fillet_polygon(hull, corner), widths


def stop_line_setbacks(link_list, node_list, pixel_per_meter):
    """Where each signalised approach's stop line belongs, off the drawn box.

    For every ≥3-arm, non-roundabout junction, each arm's setback is how far
    the painted junction patch reaches along that arm -- found by casting
    rays from the arm's mouth corners along the arm through the very outline
    :func:`junction_hulls` paints, so the line lands where the box visibly
    ends, corner fillets included.  A circle of crossing half-widths was
    tried first and landed every queue *inside* the patch, because the patch
    reaches a full arm-width past the mouths and then grows fillets.

    Returns ``{(node id, link index): setback metres}``, measured along the
    arm from its chain end at the node, with
    ``STOP_LINE_CLEARANCE_METRES`` added so the first vehicle stands a car
    length clear of the paint rather than nosing its very edge.
    """
    setbacks = {}
    for node in node_list:
        if node.is_roundabout() or node.number_of_links() < 3:
            continue
        hull, _widths = junction_outline(node, link_list, pixel_per_meter)
        if hull is None:
            continue
        xs = [p[0] for p in hull]
        ys = [p[1] for p in hull]
        for j in range(node.number_of_links()):
            link_index = node.get_link(j)
            link = link_list[link_index]
            if link.get_up_node() == node.get_id():
                seg = link.get_first_segment()
                ax, ay = seg.get_start_x(), seg.get_start_y()
                bx, by = seg.get_end_x(), seg.get_end_y()
            else:
                seg = link.get_last_segment()
                ax, ay = seg.get_end_x(), seg.get_end_y()
                bx, by = seg.get_start_x(), seg.get_start_y()
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            if length == 0.0:
                continue          # zero-length mouth: no ray to cast
            ux, uy = dx / length, dy / length
            x1, y1, x3, y3 = link_end_at_node(link, node, pixel_per_meter)
            reach = 0.0
            # Both kerbs and the middle: the fillets make the patch reach
            # further along one kerb than the other, and the queue must
            # clear the far one.
            for ox, oy in ((x1, y1), (x3, y3),
                           ((x1 + x3) / 2.0, (y1 + y3) / 2.0)):
                t = _furthest_along_ray(ox, oy, ux, uy, xs, ys)
                if t is not None:
                    reach = max(reach, t)
            if reach > 0.0:
                setbacks[(node.get_id(), link_index)] = (
                    reach / pixel_per_meter + STOP_LINE_CLEARANCE_METRES)
    return setbacks


def islands(node_list, link_list, pixel_per_meter):
    """Every roundabout island as ``(cx, cy, radius, outer_radius)``.

    The outer radius comes along because the ring between the two is the
    circulatory carriageway, and the island is only meaningful as the hole in
    the middle of it.
    """
    discs = []
    for node in node_list:
        if not node.is_roundabout():
            continue
        x_m, y_m = roundabout_centre(link_list, node)
        discs.append((x_m * pixel_per_meter, y_m * pixel_per_meter,
                      node.get_roundabout_radius() * pixel_per_meter,
                      node.get_outer_radius() * pixel_per_meter))
    return discs


def _furthest_along_ray(ox, oy, dx, dy, xs, ys):
    """How far a ray leaving (ox, oy) reaches through a convex polygon.

    Returns the largest parameter at which the ray crosses the outline, or
    ``None`` if it misses.  The ray's origin is the roundabout centre, which
    may be inside the polygon or outside it; taking the *largest* crossing
    covers both without a containment test.
    """
    best = None
    n = len(xs)
    for i in range(n):
        ax, ay = xs[i], ys[i]
        bx, by = xs[(i + 1) % n], ys[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        denominator = dx * ey - dy * ex
        if abs(denominator) < 1e-12:
            continue                       # the ray runs along this edge
        t = ((ax - ox) * ey - (ay - oy) * ex) / denominator
        along = ((ax - ox) * dy - (ay - oy) * dx) / denominator
        if t >= 0.0 and 0.0 <= along <= 1.0 and (best is None or t > best):
            best = t
    return best


def _close_notches(radii, window):
    """Round off every notch narrower than *window* steps, and nothing else.

    A morphological closing -- dilate, then erode, both with a flat window.
    Two properties earn it its place here. It can only ever *add*: the closing
    of a set contains the set, so no ground that was road stops being road,
    which is the trap every attempt at filleting these outlines falls into.
    And it is selective: a notch wider than the window survives untouched, so
    the kerb islands between neighbouring arms stay while the sharp V where a
    mouth meets the ring goes.
    """
    if window < 1:
        return radii
    n = len(radii)
    spread = range(-window, window + 1)
    dilated = [max(radii[(i + k) % n] for k in spread) for i in range(n)]
    eroded = [min(dilated[(i + k) % n] for k in spread) for i in range(n)]
    # belt and braces: the closing already dominates, this makes it exact
    # against rounding rather than against the maths.
    return [max(r, e) for r, e in zip(radii, eroded)]


def roundabout_outline(node, link_list, pixel_per_meter):
    """The whole paved area of one roundabout, as a single closed polygon.

    Drawing a roundabout as a circle and patching the gaps around it was tried
    for a long time and does not work. An arm's stated line is one kerb edge,
    so cutting it on the circle leaves the mouth lying across the ring at
    whatever angle the arm happens to arrive at -- at Khamarbari nine of the
    twelve kerbs cross the ring and three sail past it, one of them by
    fourteen metres. Every patch that closes such a wedge has a corner in it,
    and that corner belongs to the union of the patch and the arm's ribbon, so
    it cannot be rounded by reshaping either one alone.

    So the union is built directly instead. It is star-shaped about the circle
    centre -- every arm runs outwards from it -- which means its boundary is
    one radius per angle. Sweep the angles, take the furthest road at each,
    and the result joins every arm to the ring by construction: there is no
    gap to close because nothing was ever cut apart.

    What is left is the notches, and :func:`_close_notches` rounds those.
    """
    cx, cy = roundabout_centre(link_list, node)
    cx *= pixel_per_meter
    cy *= pixel_per_meter
    outer = node.get_outer_radius() * pixel_per_meter
    if outer <= 0:
        return []
    reach = outer * ROUNDABOUT_REACH

    # Every road quad that comes near the circle, arms and all.  Segment quads
    # rather than the mitred ribbons: near a junction a link is one straight
    # segment, where the two agree, and a quad is convex, which the ray test
    # below needs and a ribbon does not promise.
    quads = []
    for j in range(node.number_of_links()):
        link = link_list[node.get_link(j)]
        for k in range(link.get_number_of_segments()):
            seg = link.get_segment(k)
            x1 = seg.get_start_x() * pixel_per_meter
            y1 = seg.get_start_y() * pixel_per_meter
            x2 = seg.get_end_x() * pixel_per_meter
            y2 = seg.get_end_y() * pixel_per_meter
            if min(math.hypot(x1 - cx, y1 - cy),
                   math.hypot(x2 - cx, y2 - cy)) > reach:
                continue
            w = seg.get_segment_width() * pixel_per_meter
            # zero-length: no perpendicular, NaN corners -- skip (see
            # segment_quads)
            if math.hypot(x2 - x1, y2 - y1) <= 1e-9 or w <= 0.0:
                continue
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            x4 = Utilities.return_x4(x1, y1, x2, y2, w)
            y4 = Utilities.return_y4(x1, y1, x2, y2, w)
            quads.append(([x1, x2, x4, x3], [y1, y2, y4, y3]))

    steps = max(ROUNDABOUT_MIN_STEPS, circle_steps(reach))
    radii = []
    for i in range(steps):
        angle = 2.0 * math.pi * i / steps
        dx, dy = math.cos(angle), math.sin(angle)
        furthest = outer
        for qxs, qys in quads:
            t = _furthest_along_ray(cx, cy, dx, dy, qxs, qys)
            if t is not None and t > furthest:
                furthest = min(t, reach)
        radii.append(furthest)
    radii = _close_notches(
        radii, int(round(ROUNDABOUT_CORNER_DEGREES / 360.0 * steps)))

    # Where the radius jumps between two samples the boundary is not turning,
    # it is a corner -- the outer end of an arm's mouth, where the road stops
    # and the ring takes over.  Joining the two samples with a plain chord
    # slices that corner off, and the sliver it takes is road: up to a metre
    # of it, at exactly the place a reader looks to see whether the arm is
    # joined on.  Square it off instead, by carrying the outer radius across
    # the step.  That overshoots by at most one step the other way, which
    # lands inside the arm and is covered by the arm.
    corner = outer * ROUNDABOUT_CORNER_JUMP
    points = []
    for i in range(steps):
        here = 2.0 * math.pi * i / steps
        next_ = 2.0 * math.pi * ((i + 1) % steps) / steps
        r_here, r_next = radii[i], radii[(i + 1) % steps]
        if r_next - r_here > corner:
            points.append((cx + r_here * math.cos(here),
                           cy + r_here * math.sin(here)))
            points.append((cx + r_next * math.cos(here),
                           cy + r_next * math.sin(here)))
        elif r_here - r_next > corner:
            points.append((cx + r_here * math.cos(here),
                           cy + r_here * math.sin(here)))
            points.append((cx + r_here * math.cos(next_),
                           cy + r_here * math.sin(next_)))
        else:
            points.append((cx + r_here * math.cos(here),
                           cy + r_here * math.sin(here)))
    return points


def _arm_at_node(link, node, pixel_per_meter):
    """Mouth centre, inward unit vector and width of one arm, in pixel space.

    "Inward" points from the mouth towards the junction, which is the direction
    a vehicle entering the junction travels, and the tangent a turn leaving
    this arm has to start on.
    """
    if link.get_up_node() == node.get_id():
        seg = link.get_first_segment()
        ax, ay = seg.get_start_x(), seg.get_start_y()
        bx, by = seg.get_end_x(), seg.get_end_y()
    else:
        seg = link.get_last_segment()
        ax, ay = seg.get_end_x(), seg.get_end_y()
        bx, by = seg.get_start_x(), seg.get_start_y()

    # (ax, ay) is the node end and (bx, by) is further along the link, so this
    # runs away from the junction; the inward tangent is its negation.  Tested
    # before the kerb corners are asked for, because a zero-length segment
    # divides by zero on the way to its perpendicular.
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None

    x1, y1, x3, y3 = link_end_at_node(link, node, pixel_per_meter)
    mx, my = (x1 + x3) / 2.0, (y1 + y3) / 2.0
    width = math.hypot(x3 - x1, y3 - y1)
    return (mx, my, -dx / length, -dy / length, width)


def _movement_allowed(link, node, oneway_ids, arriving):
    """Whether traffic may arrive at, or depart from, this node on this arm.

    A two-way link permits both. A one-way link runs from its up node to its
    down node, so it can only deliver traffic to the node it ends at and only
    take traffic away from the node it starts at.
    """
    if link.get_id() not in oneway_ids:
        return True
    if arriving:
        return link.get_down_node() == node.get_id()
    return link.get_up_node() == node.get_id()


def turn_connectors(node_list, link_list, pixel_per_meter):
    """A curved carriageway ribbon for every turning movement at every junction.

    Each ribbon is a cubic Bezier swept to the narrower of the two arms'
    widths, entering along one arm's tangent and leaving along the other's, so
    the junction reads as a set of turning paths rather than one blunt polygon.

    Movements are generated per unordered pair of arms: the ribbon a vehicle
    turning from A to B drives over is the same shape as the one from B to A,
    so drawing both would only pay twice for the same polygon.
    """
    oneway_ids = getattr(Parameters, "ONEWAY_LINKS", None) or set()
    # A roundabout has no turning paths to draw: every movement is the same
    # one-way ring, and a Bezier from one arm to another would cut straight
    # across the island.
    ribbons = []
    for node in node_list:
        if node.number_of_links() < 2 or node.is_roundabout():
            continue
        arms = []
        for j in range(node.number_of_links()):
            link = link_list[node.get_link(j)]
            arm = _arm_at_node(link, node, pixel_per_meter)
            if arm is not None:
                arms.append((link, arm))

        for i in range(len(arms)):
            for k in range(i + 1, len(arms)):
                link_a, (ax, ay, aux, auy, aw) = arms[i]
                link_b, (bx, by, bux, buy, bw) = arms[k]
                # Skip the pair only when neither direction is drivable.
                forward = (_movement_allowed(link_a, node, oneway_ids, True)
                           and _movement_allowed(link_b, node, oneway_ids, False))
                backward = (_movement_allowed(link_b, node, oneway_ids, True)
                            and _movement_allowed(link_a, node, oneway_ids, False))
                if not (forward or backward):
                    continue

                chord = math.hypot(bx - ax, by - ay)
                if chord <= 1e-9:
                    continue
                reach = chord * CONNECTOR_TENSION
                p0 = (ax, ay)
                p1 = (ax + aux * reach, ay + auy * reach)
                p2 = (bx + bux * reach, by + buy * reach)
                p3 = (bx, by)

                half = min(aw, bw) / 2.0
                if half <= 0.0:
                    continue

                left, right = [], []
                for step in range(CONNECTOR_STEPS + 1):
                    t = step / CONNECTOR_STEPS
                    u = 1.0 - t
                    px = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
                          + 3 * u * t * t * p2[0] + t * t * t * p3[0])
                    py = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
                          + 3 * u * t * t * p2[1] + t * t * t * p3[1])
                    # derivative of the same cubic, for the normal
                    tx = (3 * u * u * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0])
                          + 3 * t * t * (p3[0] - p2[0]))
                    ty = (3 * u * u * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1])
                          + 3 * t * t * (p3[1] - p2[1]))
                    tlen = math.hypot(tx, ty)
                    if tlen <= 1e-9:
                        continue
                    nx, ny = -ty / tlen * half, tx / tlen * half
                    left.append((px + nx, py + ny))
                    right.append((px - nx, py - ny))
                if len(left) < 2:
                    continue
                poly = left + right[::-1]
                ribbons.append(([p[0] for p in poly], [p[1] for p in poly]))
    return ribbons


class _WiderSegment:
    """A segment that reports a wider carriageway than the model has.

    Everything :func:`build` measures a road by, it asks the segment for, so
    inflating that one answer widens the drawn road and nothing else.  The
    simulation never sees this: strips, capacity, speeds and every statistic
    come from the real ``Segment``, which is untouched.
    """

    __slots__ = ("_seg", "_extra", "_sx", "_sy", "_ex", "_ey")

    def __init__(self, seg, extra):
        self._seg = seg
        self._extra = extra
        # The painter spans the band from the stated kerb along the segment
        # normal, so widening only the width grows it entirely on the far
        # side: the drawn band slides half the extra off the road it is
        # meant to cover, and roadside objects on the stated side poke
        # outside the kerb.  Shift the stated kerb back by half the extra so
        # the widening grows both kerbs equally.
        x1, y1 = seg.get_start_x(), seg.get_start_y()
        x2, y2 = seg.get_end_x(), seg.get_end_y()
        length = math.hypot(x2 - x1, y2 - y1)
        if length > 0:
            nx = -(y2 - y1) / length * extra / 2.0
            ny = (x2 - x1) / length * extra / 2.0
        else:
            nx = ny = 0.0
        self._sx, self._sy = x1 - nx, y1 - ny
        self._ex, self._ey = x2 - nx, y2 - ny

    def get_segment_width(self):
        return self._seg.get_segment_width() + self._extra

    def get_start_x(self):
        return self._sx

    def get_start_y(self):
        return self._sy

    def get_end_x(self):
        return self._ex

    def get_end_y(self):
        return self._ey

    def __getattr__(self, name):
        return getattr(self._seg, name)


class _WiderLink:
    __slots__ = ("_link", "_extra")

    def __init__(self, link, extra):
        self._link = link
        self._extra = extra

    def get_segment(self, i):
        return _WiderSegment(self._link.get_segment(i), self._extra)

    def get_first_segment(self):
        return self.get_segment(0)

    def get_last_segment(self):
        return self.get_segment(self._link.get_number_of_segments() - 1)

    def __getattr__(self, name):
        return getattr(self._link, name)


def widened(link_list, extra_metres):
    """*link_list* with every carriageway *extra_metres* wider, for drawing.

    A cartographic road width is a choice, not a measurement: OpenStreetMap
    draws a trunk road about eleven metres wide whatever it really is, and a
    fourteen-metre survey carriageway laid over it leaves the casing sticking
    out on both sides, which reads as the model being in the wrong place.
    Widening what is *drawn* closes that without touching what is simulated.
    """
    if extra_metres <= 0.0:
        return link_list
    return [_WiderLink(link, extra_metres) for link in link_list]


def build(link_list, node_list, pixel_per_meter, widen=0.0):
    """Precompute everything :func:`paint` needs.

    The result depends only on the network and *pixel_per_meter*, so a renderer
    that draws many frames should build it once and pass it back in.

    *widen* adds that many metres to every carriageway, for drawing only --
    see :func:`widened`.  It changes the picture and nothing else, so a
    renderer that turns it on must rebuild rather than reuse a cached result.

    The quads come first and are still built, but nothing paints them any
    more: the carriageway is drawn from the ribbons at the end, and the quads
    are what :func:`junction_hulls` probes against to decide which of a
    junction's edges are mouths.  A quad is convex and a ribbon is not, which
    is the whole reason the probe still wants them.
    """
    link_list = widened(link_list, widen)
    quads = segment_quads(link_list, pixel_per_meter)
    boundary = frozenset(node.get_id() for node in node_list
                         if node.number_of_links() == 1)
    return (quads,
            junction_hulls(node_list, link_list, pixel_per_meter, quads),
            islands(node_list, link_list, pixel_per_meter),
            turn_connectors(node_list, link_list, pixel_per_meter),
            lane_markings(link_list, pixel_per_meter),
            carriageways(link_list, pixel_per_meter, boundary))


def paint(g, link_list, node_list, pixel_per_meter, geometry=None,
          fill=True) -> None:
    """Paint the whole road network onto the graphics surface *g*.

    Draw order matters: road surfaces, then kerb outlines, then the painted
    lane dividers, then the junction patches and the turning connectors on top
    so they cover the kerb stubs and the markings and the intersection reads as
    one smooth area, then each roundabout island last of all so the circulatory
    carriageway reads as a ring.

    With *fill* off the carriageway is washed rather than painted: the same
    areas are filled, in a near-white grey at part opacity, so the road still
    reads as a surface while the imagery underneath shows through it.  That is
    the mode for drawing over a photograph, where the solid road colour would
    hide exactly what the reader wants to see.  Nothing moves between the two
    modes and nothing is added; only the colour and the opacity change.

    A Tk canvas has no alpha, so there the wash is a stipple; the report's SVG
    surface renders the same call as real opacity.
    """
    _quads, hulls, discs, connectors, markings, carriage = (
        geometry if geometry is not None
        else build(link_list, node_list, pixel_per_meter))

    # Over imagery the outline is the only thing describing the carriageway,
    # so it is drawn wider and in a colour chosen to sit on a photograph
    # rather than on the blank canvas.
    border = (Constants.road_border_color if fill
              else Constants.overlay_border_color)
    marking = (Constants.lane_marking_color if fill
               else Constants.overlay_marking_color)
    surface = (Constants.road_fill_color if fill
               else Constants.overlay_fill_color)
    kerb_width = ((KERB_WIDTH_METRES if fill else OVERLAY_KERB_WIDTH_METRES)
                  * pixel_per_meter)
    alpha = 1.0 if fill else Constants.OVERLAY_FILL_ALPHA

    # One polygon per link rather than one per segment: a bend leaves a wedge
    # between neighbouring segments' far corners, and filling the whole chain
    # at once is what closes it.  See :func:`carriageways`.
    g.set_alpha(alpha)
    g.set_color(surface)
    for near, far in carriage:
        outline = near + far[::-1]
        g.fill_polygon([p[0] for p in outline], [p[1] for p in outline],
                       len(outline))
    g.set_alpha(1.0)

    g.set_color(border)
    g.set_stroke(kerb_width)
    for near, far in carriage:
        # Each kerb is one polyline down the length of the link, so it turns
        # through a bend rather than stopping and restarting at every joint.
        for edge in (near, far):
            for i in range(len(edge) - 1):
                g.draw_line(edge[i][0], edge[i][1],
                            edge[i + 1][0], edge[i + 1][1])

    # Painted dividers, on the road surface but under the junction patch: the
    # markings of a real approach stop at the intersection rather than being
    # ruled straight across it.
    g.set_color(marking)
    g.set_stroke(MARKING_WIDTH_METRES * pixel_per_meter)
    for x1, y1, x2, y2 in markings:
        g.draw_line(x1, y1, x2, y2)

    # The junction patch and the turning paths, in the same surface colour so
    # the intersection reads as one area.  They go on after the kerb outlines
    # so a kerb stub is not left drawn across a mouth.
    #
    # Over imagery these are drawn opaque even though the arms are washed: the
    # patch, the connectors and the arms all overlap here, and three washes
    # stacked come out darker than one, which would paint a blotch exactly
    # where the junction is.
    # Same wash as the arms rather than a stronger one.  The patch, the
    # connectors and the arms all overlap here, so anything heavier stacks
    # into a blotch exactly where the junction is.
    g.set_alpha(1.0 if fill else alpha)
    g.set_color(surface)
    for xs, ys, cx, cy, radius, _kerb in hulls:
        g.fill_polygon(xs, ys, len(xs))
        if radius > 0:
            g.fill_oval(cx - radius, cy - radius, radius * 2, radius * 2)
    for xs, ys in connectors:
        g.fill_polygon(xs, ys, len(xs))
    g.set_alpha(1.0)

    # The junction's own kerb, drawn last so it runs continuously round each
    # corner and the arms' kerbs meet it flush instead of stopping in mid-air.
    g.set_color(border)
    g.set_stroke(kerb_width)
    for _xs, _ys, _cx, _cy, _radius, kerb in hulls:
        for x1, y1, x2, y2 in kerb:
            g.draw_line(x1, y1, x2, y2)

    # The island last, and solid even over imagery.  It is not road and never
    # was: the ring around it is the carriageway, and the whole point of
    # painting the island opaque is that nothing shows through a thing you
    # cannot drive on.  It also covers the far ends of the arms, which the
    # network still draws running to the middle of the circle.
    for cx, cy, r, _outer in discs:
        g.set_alpha(1.0)
        g.set_color(Constants.island_fill_color)
        g.fill_oval(cx - r, cy - r, r * 2, r * 2)
        g.set_color(border)
        g.set_stroke(kerb_width)
        g.draw_oval(cx - r, cy - r, r * 2, r * 2)


# ---------------------------------------------------------------------------
# signal heads
# ---------------------------------------------------------------------------

#: How far back from the junction mouth the stop-line bar sits, in metres --
#: roughly where a first vehicle actually halts, and clear of the junction
#: patch so the bar is never painted over.
SIGNAL_SETBACK_METRES = 1.4

#: Thickness of the bar, in metres.  Wide enough to read at a whole-network
#: zoom; still a stop line rather than a wall at a junction close-up.
SIGNAL_BAR_METRES = 1.6

#: The bar is drawn on a slightly wider near-black casing.  Without it a red
#: bar is the size and colour of a car and a green one the colour of a CNG;
#: the casing is what makes it read as road furniture rather than traffic.
SIGNAL_CASING_METRES = 2.4
SIGNAL_CASING_COLOUR = Color(24, 26, 28)

#: Plain traffic-light colours, deliberately not the vehicle palette: the CNG
#: is already green and a signal must never be mistaken for a vehicle.
SIGNAL_COLOURS = {
    "GREEN": Color(52, 200, 82),
    "YELLOW": Color(240, 195, 45),
    "RED": Color(232, 48, 38),
}


def signal_bars(g, link_list, node_list, pixel_per_meter) -> None:
    """VISSIM-style stop-line signal bars, one per signalised approach.

    Every junction the processor signals gets a coloured bar across each
    approach at the mouth, red or green with that approach's bundle -- the
    way VISSIM draws a signal head in its 2D view.  The bar spans only the
    *approaching* half of the carriageway: the half whose strips travel
    towards this node, which is the traffic the signal is speaking to.

    Signal state changes every few steps, so this belongs to the dynamic part
    of a frame -- the GUI calls it after ``begin_dynamic`` and the report
    captures it per frame -- never to the cached road geometry.

    Roundabouts are skipped: they have no phases, only a give-way rule, and a
    ring drawn with flickering red bars reads as a broken signal.
    """
    ppm = pixel_per_meter
    fp = Parameters.footpath_strip_width
    sw = Parameters.strip_width
    for node in node_list:
        if not node.is_signalised() or node.is_roundabout():
            continue
        for j in range(node.number_of_links()):
            link_index = node.get_link(j)
            link = link_list[link_index]
            # The same up/down convention the junction patch and the stop
            # lines use (junction_outline), so the bar, the patch edge and
            # the queue all agree about which chain end is the mouth.
            at_end = link.get_up_node() != node.get_id()
            # The segment that actually carries the stop line: a box deeper
            # than a short mouth segment walks its line back into earlier
            # segments (Processor._set_stop_lines), and a bar drawn on the
            # mouth regardless stood in the middle of the drawn box.  A
            # segment wholly inside the box carries its full length as its
            # setback, so the carrier is the first one, walking inward from
            # the node, with room to spare -- which is the mouth itself
            # whenever no line is set at all.
            count = link.get_number_of_segments()
            order = range(count - 1, -1, -1) if at_end else range(count)
            seg = None
            for i in order:
                candidate = link.get_segment(i)
                held = (candidate.stop_setback_at_end if at_end
                        else candidate.stop_setback_at_start)
                if held < candidate.get_length() - 1e-9:
                    seg = candidate
                    break
            if seg is None:
                seg = link.get_last_segment() if at_end \
                    else link.get_first_segment()

            # Which half of the carriageway drives towards this node: the
            # low strips travel start->end of their segment, the high strips
            # the other way.  A one-way link keeps its whole width for the
            # up->down direction, so its entry end gets no bar at all.
            oneway = link.get_id() in Parameters.ONEWAY_LINKS
            low = getattr(seg, "middle_low_strip_index", None)
            high = getattr(seg, "middle_high_strip_index", None)
            last_strip = getattr(seg, "last_vehicle_strip_index", None)
            width_m = seg.get_segment_width()
            if oneway:
                if not at_end:
                    continue          # traffic leaves the node here
                w_a, w_b = fp, max(width_m - fp, fp)
            elif at_end:
                w_a = fp
                w_b = (fp + low * sw) if low is not None else width_m / 2.0
            else:
                w_a = (fp + (high - 1) * sw) if high is not None \
                    else width_m / 2.0
                w_b = (fp + last_strip * sw) if last_strip is not None \
                    else max(width_m - fp, width_m / 2.0)

            sx = seg.get_start_x() * ppm
            sy = seg.get_start_y() * ppm
            ex = seg.get_end_x() * ppm
            ey = seg.get_end_y() * ppm
            length = math.hypot(ex - sx, ey - sy)
            if length == 0.0:
                continue              # zero-length mouth: nowhere to draw
            # Where KeepClearMode has placed a stop line, the bar sits just
            # ahead of it, so the queue's noses stand behind the bar; without
            # one it sits a nominal stride back from the mouth.
            stop = (getattr(seg, "stop_setback_at_end", 0.0) if at_end
                    else getattr(seg, "stop_setback_at_start", 0.0))
            if Parameters.KEEP_CLEAR_MODE and stop > 0.0:
                setback_m = max(stop - 0.6, 0.2)
                # The carrier segment holds its line by construction; only
                # keep the bar off the segment's far boundary.
                setback = min(setback_m * ppm, length * 0.95)
            else:
                setback_m = SIGNAL_SETBACK_METRES
                setback = min(setback_m * ppm, length * 0.45)
            t = (length - setback) / length if at_end else setback / length

            state = node.get_signal_on_link(link_index)
            colour = SIGNAL_COLOURS.get(
                getattr(state, "name", str(state)), SIGNAL_COLOURS["RED"])
            points = []
            for w in (w_a * ppm, w_b * ppm):
                ax = Utilities.return_x3(sx, sy, ex, ey, w)
                ay = Utilities.return_y3(sx, sy, ex, ey, w)
                bx = Utilities.return_x4(sx, sy, ex, ey, w)
                by = Utilities.return_y4(sx, sy, ex, ey, w)
                points.append((ax + (bx - ax) * t, ay + (by - ay) * t))
            g.set_color(SIGNAL_CASING_COLOUR)
            g.set_stroke(SIGNAL_CASING_METRES * ppm)
            g.draw_line(points[0][0], points[0][1],
                        points[1][0], points[1][1])
            g.set_color(colour)
            g.set_stroke(SIGNAL_BAR_METRES * ppm)
            g.draw_line(points[0][0], points[0][1],
                        points[1][0], points[1][1])
