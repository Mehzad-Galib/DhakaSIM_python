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
from . import utilities as Utilities

#: Kerb outline width, in metres. At the GUI's 15 px/m this is the 15-pixel
#: stroke the road outlines have always used.
KERB_WIDTH_METRES = 1.0


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
            x3 = Utilities.return_x3(x1, y1, x2, y2, w)
            y3 = Utilities.return_y3(x1, y1, x2, y2, w)
            x4 = Utilities.return_x4(x1, y1, x2, y2, w)
            y4 = Utilities.return_y4(x1, y1, x2, y2, w)
            quads.append(([x1, x2, x4, x3], [y1, y2, y4, y3]))
    return quads


def junction_hulls(node_list, link_list, pixel_per_meter):
    """A filled convex patch for every junction (node with >1 link).

    The patch is built from each incident link's two kerb corners at the node
    *and* the same corners carried a short way along the link, so the patch
    overlaps the road surfaces and leaves no notch at the mouth.
    """
    hulls = []
    for node in node_list:
        if node.number_of_links() < 2:
            continue
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
        if len(hull) >= 3:
            # A disc at the junction centre rounds off any concave mouth the
            # convex hull cannot reach.
            cx = sum(p[0] for p in hull) / len(hull)
            cy = sum(p[1] for p in hull) / len(hull)
            radius = max(widths) * pixel_per_meter * 0.5
            hulls.append(([p[0] for p in hull], [p[1] for p in hull],
                          cx, cy, radius))
    return hulls


def islands(node_list, link_list, pixel_per_meter):
    """Centre and radius of every roundabout island, in pixel space."""
    discs = []
    for node in node_list:
        if not node.is_roundabout():
            continue
        x_m, y_m = node_point(link_list, node)
        discs.append((x_m * pixel_per_meter, y_m * pixel_per_meter,
                      node.get_roundabout_radius() * pixel_per_meter))
    return discs


def build(link_list, node_list, pixel_per_meter):
    """Precompute everything :func:`paint` needs.

    The result depends only on the network and *pixel_per_meter*, so a renderer
    that draws many frames should build it once and pass it back in.
    """
    return (segment_quads(link_list, pixel_per_meter),
            junction_hulls(node_list, link_list, pixel_per_meter),
            islands(node_list, link_list, pixel_per_meter))


def paint(g, link_list, node_list, pixel_per_meter, geometry=None) -> None:
    """Paint the whole road network onto the graphics surface *g*.

    Draw order matters: road surfaces, then kerb outlines, then the junction
    patches on top so they cover the kerb stubs and the intersection reads as
    one smooth area, then each roundabout island last of all so the circulatory
    carriageway reads as a ring.
    """
    quads, hulls, discs = (geometry if geometry is not None
                           else build(link_list, node_list, pixel_per_meter))

    g.set_color(Constants.road_fill_color)
    for xs, ys in quads:
        g.fill_polygon(xs, ys, 4)

    g.set_color(Constants.road_border_color)
    g.set_stroke(KERB_WIDTH_METRES * pixel_per_meter)
    for xs, ys in quads:
        # the two long edges of the quad are the kerbs
        g.draw_line(xs[0], ys[0], xs[1], ys[1])
        g.draw_line(xs[3], ys[3], xs[2], ys[2])

    g.set_color(Constants.road_fill_color)
    for xs, ys, cx, cy, radius in hulls:
        g.fill_polygon(xs, ys, len(xs))
        g.fill_oval(cx - radius, cy - radius, radius * 2, radius * 2)

    for cx, cy, r in discs:
        g.set_color(Constants.island_fill_color)
        g.fill_oval(cx - r, cy - r, r * 2, r * 2)
        g.set_color(Constants.road_border_color)
        g.set_stroke(KERB_WIDTH_METRES * pixel_per_meter)
        g.draw_oval(cx - r, cy - r, r * 2, r * 2)
