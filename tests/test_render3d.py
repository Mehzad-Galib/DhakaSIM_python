"""Pin the 3D surface's economies: line art, chained road lines, the static
layer, part culling and aerial haze.

All of them are cost reductions or depth cues, and a cost reduction that
quietly drops the wrong thing looks exactly like one that works -- the frame
gets faster and the picture gets worse in a way nobody notices until they
look closely.  So what each of them is allowed to leave out is pinned here.

Run with ``python tests/test_render3d.py``.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim import render3d                     # noqa: E402
from dhakasim.parameters import Parameters        # noqa: E402
from dhakasim.javacompat import Color             # noqa: E402
from dhakasim.render3d import Scene3D             # noqa: E402


class _Recorder:
    """Just enough of ``tkinter.Canvas`` to count what a frame asks for."""

    def __init__(self):
        self.items = []

    def configure(self, **_kw):
        pass

    def delete(self, *_args):
        self.items = []

    def create_rectangle(self, *a, **kw):
        self.items.append(("rectangle", a, kw))

    def create_polygon(self, *a, **kw):
        self.items.append(("polygon", a, kw))

    def create_line(self, *a, **kw):
        self.items.append(("line", a, kw))

    def create_text(self, *a, **kw):
        self.items.append(("text", a, kw))

    def tag_raise(self, *a):
        self.items.append(("raise", a, {}))

    def kinds(self, kind):
        return [item for item in self.items if item[0] == kind]


def _scene(style="line", width=900, height=500):
    canvas = _Recorder()
    scene = Scene3D(canvas)
    scene.style = style
    scene.set_ground_extent(0.0, 0.0, 400.0, 400.0)
    scene.camera.frame(0.0, 0.0, 400.0, 400.0)
    scene.camera.pitch = math.radians(20.0)
    scene.begin_frame(width, height, 15.0)
    # begin_frame lays down the sky, the ground and the horizon before any
    # caller draws anything; forget them, so a count here is a count of what
    # the test itself asked for.
    canvas.items = []
    return scene, canvas


def _a_vehicle(scene, x=200.0, y=200.0, length=60.0, width=25.0):
    """Stand one model on a footprint, the way the paint loop does."""
    scene.set_color(Color(210, 50, 50))
    scene.begin_prop("vehicle", 4)              # a car
    scene.fill_polygon([x, x + length, x + length, x],
                       [y, y, y + width, y + width], 4)
    scene.end_prop()


# --------------------------------------------------------------------------
# the silhouette
# --------------------------------------------------------------------------

def test_the_hull_drops_a_point_inside_it():
    out = render3d._silhouette([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)])
    assert len(out) == 8, out            # four corners, x and y each


def test_the_hull_survives_degenerate_input():
    assert render3d._silhouette([]) == []
    assert render3d._silhouette([(1, 1)]) == []
    assert render3d._silhouette([(1, 1), (2, 2)]) == []
    assert render3d._silhouette([(0, 0), (1, 1), (2, 2)]) == []   # collinear


def test_the_hull_is_a_closed_ring_in_order():
    """Consecutive points must be neighbours on the outline, or the polygon
    Tk draws is a bow tie rather than a box."""
    out = render3d._silhouette([(0, 0), (10, 0), (10, 10), (0, 10)])
    points = [(out[i], out[i + 1]) for i in range(0, len(out), 2)]
    area = 0.0
    for i in range(len(points)):
        ax, ay = points[i]
        bx, by = points[(i + 1) % len(points)]
        area += ax * by - bx * ay
    assert abs(area) / 2.0 == 100.0, points


# --------------------------------------------------------------------------
# line art against solid
# --------------------------------------------------------------------------

def test_line_art_draws_far_fewer_polygons_than_solid():
    """The whole point.  A box shows the camera up to three faces; line art
    draws its silhouette once instead."""
    line_scene, line_canvas = _scene("line")
    _a_vehicle(line_scene)
    line_scene.flush()
    solid_scene, solid_canvas = _scene("solid")
    _a_vehicle(solid_scene)
    solid_scene.flush()
    line_count = len(line_canvas.kinds("polygon"))
    solid_count = len(solid_canvas.kinds("polygon"))
    assert line_count < solid_count, (line_count, solid_count)
    assert solid_count >= 2 * line_count, (line_count, solid_count)


def test_both_styles_actually_draw_the_vehicle():
    for style in ("line", "solid"):
        scene, canvas = _scene(style)
        _a_vehicle(scene)
        scene.flush()
        assert canvas.kinds("polygon"), style


def test_line_art_outlines_every_part_it_fills():
    scene, canvas = _scene("line")
    _a_vehicle(scene)
    scene.flush()
    for _kind, _args, kw in canvas.kinds("polygon"):
        if kw.get("fill") == render3d.SHADOW_COLOR:
            continue
        assert kw.get("outline"), "a line-art part with no line on it"


def test_a_pale_body_still_gets_a_dark_enough_line():
    """A white truck outlined in near-white is invisible on a white road."""
    for rgb in ((255, 255, 255), (240, 240, 245), (210, 50, 50), (30, 160, 70)):
        _fill, outline = render3d._line_colours(rgb)
        r = int(outline[1:3], 16)
        g = int(outline[3:5], 16)
        b = int(outline[5:7], 16)
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        assert luma <= render3d.LINE_LUMA_CEILING + 1.0, (rgb, outline, luma)


def test_the_wash_keeps_the_body_colour_it_came_from():
    """A bus has to stay purple and a CNG green, or the view stops agreeing
    with the legend and the 2D picture."""
    red, _ = render3d._line_colours((210, 50, 50))
    green, _ = render3d._line_colours((30, 160, 70))
    assert red != green
    assert int(red[1:3], 16) > int(red[3:5], 16)      # still reddest
    assert int(green[3:5], 16) > int(green[1:3], 16)  # still greenest


# --------------------------------------------------------------------------
# chained road lines
# --------------------------------------------------------------------------

def test_a_kerb_run_becomes_one_polyline():
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    for i in range(6):
        scene.draw_line(100.0 + i * 30.0, 200.0, 130.0 + i * 30.0, 200.0)
    scene.flush()
    lines = canvas.kinds("line")
    assert len(lines) == 1, len(lines)
    assert len(lines[0][1]) == 2 * 7, "every point of the run has to survive"


def test_a_broken_run_is_two_polylines():
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(100.0, 200.0, 160.0, 200.0)
    scene.draw_line(160.0, 200.0, 220.0, 200.0)
    scene.draw_line(300.0, 260.0, 360.0, 260.0)      # somewhere else entirely
    scene.flush()
    assert len(canvas.kinds("line")) == 2


def test_a_lone_speck_of_road_paint_is_dropped():
    """The lane markings: 979 of them at Khamarbari, three pixels each."""
    scene, canvas = _scene("line")
    scene.set_color(Color(152, 160, 170))
    scene.set_stroke(1.0)
    scene.draw_line(200.0, 200.0, 200.2, 200.0)
    scene.flush()
    assert canvas.kinds("line") == []


def test_a_short_line_inside_a_run_is_never_dropped():
    """A kerb is made of short segments too, and dropping one gaps it."""
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(200.0, 200.0, 200.2, 200.0)
    scene.draw_line(200.2, 200.0, 260.0, 200.0)
    scene.flush()
    lines = canvas.kinds("line")
    assert len(lines) == 1
    assert len(lines[0][1]) == 6, "the short first segment was dropped"


def test_a_chain_is_drawn_before_whatever_follows_it():
    """Paint order is call order here.  A kerb held in the buffer while a
    junction patch is filled would come back out on top of it."""
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(100.0, 200.0, 160.0, 200.0)
    scene.set_color(Color(246, 247, 249))
    scene.fill_polygon([100.0, 300.0, 300.0, 100.0],
                       [100.0, 100.0, 300.0, 300.0], 4)
    scene.flush()
    kinds = [item[0] for item in canvas.items]
    assert "line" in kinds and "polygon" in kinds
    assert kinds.index("line") < kinds.index("polygon"), kinds


def test_an_arrow_is_never_swallowed_into_a_chain():
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(100.0, 200.0, 160.0, 200.0)
    scene.draw_line(160.0, 200.0, 220.0, 200.0, arrow=True)
    scene.flush()
    arrows = [kw for _k, _a, kw in canvas.kinds("line") if "arrow" in kw]
    assert len(arrows) == 1


# --------------------------------------------------------------------------
# the static layer
# --------------------------------------------------------------------------

def test_keep_static_repaints_no_sky_or_ground():
    """A reused frame is vehicles only; the sky and ground survive on the
    canvas from the frame that painted them."""
    scene, canvas = _scene("solid")
    scene.begin_frame(900, 500, 15.0, keep_static=True)
    assert canvas.kinds("rectangle") == []
    _a_vehicle(scene)
    scene.flush()
    assert canvas.kinds("polygon")


def test_static_and_dynamic_items_carry_their_tags():
    """The whole reuse contract: the caller deletes DYNAMIC_TAG between
    frames, so a road item tagged dynamic would vanish with the vehicles and
    a vehicle tagged static would smear across every following frame."""
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(100.0, 200.0, 160.0, 200.0)
    scene.begin_dynamic()
    _a_vehicle(scene)
    scene.flush()
    lines = canvas.kinds("line")
    assert lines and all(
        kw.get("tags") == Scene3D.STATIC_TAG for _k, _a, kw in lines), lines
    polygons = canvas.kinds("polygon")
    assert polygons and all(
        kw.get("tags") == Scene3D.DYNAMIC_TAG for _k, _a, kw in polygons)


def test_begin_dynamic_flushes_the_chain_first():
    """A kerb run still buffered when the tag flips would be drawn later with
    the dynamic tag and deleted with the vehicles next frame."""
    scene, canvas = _scene("line")
    scene.set_color(Color(58, 65, 73))
    scene.set_stroke(1.0)
    scene.draw_line(100.0, 200.0, 160.0, 200.0)
    scene.begin_dynamic()
    lines = canvas.kinds("line")
    assert len(lines) == 1
    assert lines[0][2].get("tags") == Scene3D.STATIC_TAG


def test_a_reused_frame_lifts_the_labels_back_on_top():
    """Node names live in the static layer under freshly created vehicles;
    flush must raise them, exactly as a full frame draws them last."""
    scene, canvas = _scene("solid")
    scene.begin_frame(900, 500, 15.0, keep_static=True)
    _a_vehicle(scene)
    scene.flush()
    raises = canvas.kinds("raise")
    assert raises and raises[-1][1] == (Scene3D.LABEL_TAG,), raises


def test_a_full_frame_grades_the_sky_and_the_ground():
    """The gradient bands are the static layer's polish; a flat frame means
    they were lost, a dynamic-tagged band means they get deleted per frame."""
    canvas = _Recorder()
    scene = Scene3D(canvas)
    scene.camera.frame(0.0, 0.0, 400.0, 400.0)
    scene.camera.pitch = math.radians(20.0)   # horizon well on screen
    scene.begin_frame(900, 500, 15.0)
    bands = canvas.kinds("rectangle")
    assert len(bands) >= 10, len(bands)
    assert len({kw.get("fill") for _k, _a, kw in bands}) >= 6
    assert all(kw.get("tags") == Scene3D.STATIC_TAG for _k, _a, kw in bands)


# --------------------------------------------------------------------------
# part culling and aerial haze
# --------------------------------------------------------------------------

def test_a_mid_distance_car_loses_its_wheels_but_not_its_body():
    near_scene, near_canvas = _scene("solid")
    _a_vehicle(near_scene)
    near_scene.flush()

    far_canvas = _Recorder()
    far_scene = Scene3D(far_canvas)
    far_scene.camera.frame(0.0, 0.0, 400.0, 400.0)
    far_scene.camera.pitch = math.radians(20.0)
    # Pull the camera back until the body sits just above the whole-body LOD
    # cut: still detailed, but its wheels project under PART_MIN_PIXELS.
    far_scene.camera.distance = 2200.0
    far_scene.begin_frame(900, 500, 15.0)
    far_canvas.items = []
    _a_vehicle(far_scene)
    far_scene.flush()

    near = len(near_canvas.kinds("polygon"))
    far = len(far_canvas.kinds("polygon"))
    assert far >= 3, far                  # body, cabin and shadow survive
    assert near >= far + 4, (near, far)   # the wheels went


def test_a_far_speck_is_still_drawn_and_costs_one_item():
    """At a whole-network framing most props project under SPECK_PIXELS.
    They are how a wide view shows where the traffic is, so they must
    survive -- as exactly one canvas item each, not a box and a shadow."""
    for style in ("line", "solid"):
        canvas = _Recorder()
        scene = Scene3D(canvas)
        scene.style = style
        scene.camera.frame(0.0, 0.0, 400.0, 400.0)
        scene.camera.pitch = math.radians(20.0)
        scene.camera.distance = 30000.0
        scene.begin_frame(900, 500, 15.0)
        canvas.items = []
        _a_vehicle(scene)
        scene.flush()
        polygons = canvas.kinds("polygon")
        assert len(polygons) == 1, (style, len(polygons))
        assert polygons[0][2].get("fill"), style


def test_haze_pulls_a_distant_body_towards_the_haze_colour():
    for haze in (4, 8, 16):
        pale = render3d._lit((210, 50, 50), 0.9, haze)
        strong = render3d._lit((210, 50, 50), 0.9, 0)
        for i, channel in enumerate(("red", "green", "blue")):
            target = render3d.HAZE_RGB[i]
            hazed = int(pale[1 + 2 * i:3 + 2 * i], 16)
            plain = int(strong[1 + 2 * i:3 + 2 * i], 16)
            assert abs(hazed - target) <= abs(plain - target), (haze, channel)


def test_roads_keep_the_exact_2d_palette():
    """_lit tints and hazes; _shaded must not, or the 3D roads drift away
    from the 2D picture and the report legend."""
    assert render3d._shaded((152, 160, 170), 1.0) == "#98a0aa"


def test_a_near_vehicle_gets_no_haze():
    scene, canvas = _scene("solid")
    _a_vehicle(scene)                      # sits at the framed target
    scene.flush()
    shadows = [kw for _k, _a, kw in canvas.kinds("polygon")
               if kw.get("fill") == render3d.SHADOW_COLOR]
    assert shadows, "at the orbit distance the shadow is unhazed"


# --------------------------------------------------------------------------
# the setting
# --------------------------------------------------------------------------

def test_a_new_scene_takes_the_style_from_the_parameters():
    saved = Parameters.RENDER_3D_STYLE
    try:
        for style in ("solid", "line"):
            Parameters.RENDER_3D_STYLE = style
            assert Scene3D(_Recorder()).style == style
    finally:
        Parameters.RENDER_3D_STYLE = saved


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            else:
                print(f"ok   {name}")
    print("all passed" if not failures else f"{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
