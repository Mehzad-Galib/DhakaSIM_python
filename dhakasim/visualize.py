"""In-run visualisation as self-contained SVG (no third-party dependencies).

Captures a few frames while a simulation runs and returns an inline animated SVG
(a pure-CSS flip-book) plus a static SVG snapshot, ready to embed directly in the
HTML report. Reuses the simulator's own vehicle-drawing geometry through a small
SVG-emitting graphics backend, so it needs nothing beyond the standard library
and renders in any browser.
"""

from __future__ import annotations

from .parameters import Parameters


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class _SVGGraphics:
    """The drawing surface ``Vehicle.draw_vehicle`` expects; emits SVG."""

    def __init__(self, tx, ty):
        self._tx = tx
        self._ty = ty
        self._c = "#000000"
        self.parts = []

    def set_transform(self, *a):
        pass

    def set_color(self, c):
        self._c = c.to_hex() if hasattr(c, "to_hex") else c

    def set_stroke(self, w):
        pass

    def set_font(self, *a):
        pass

    def draw_line(self, x1, y1, x2, y2):
        self.parts.append(
            f'<line x1="{self._tx(x1):.1f}" y1="{self._ty(y1):.1f}" '
            f'x2="{self._tx(x2):.1f}" y2="{self._ty(y2):.1f}" stroke="{self._c}"/>')

    def fill_polygon(self, xs, ys, n):
        pts = " ".join(f"{self._tx(xs[i]):.1f},{self._ty(ys[i]):.1f}"
                       for i in range(n))
        self.parts.append(f'<polygon points="{pts}" fill="{self._c}"/>')

    def fill_oval(self, x, y, w, h):
        self.parts.append(
            f'<ellipse cx="{self._tx(x) + w / 2:.1f}" cy="{self._ty(y) + h / 2:.1f}" '
            f'rx="{w / 2:.1f}" ry="{h / 2:.1f}" fill="{self._c}"/>')

    def draw_string(self, *a):
        pass


class RunRecorder:
    """Captures frames during a run and encodes them as SVG for the report."""

    def __init__(self, processor, ppm=2.5, max_frames=24, margin=60):
        self.ok = False
        self.frames = []
        if max_frames <= 0:
            return
        self.proc = processor

        roads = []
        xs, ys = [], []
        for link in processor.link_list:
            for j in range(link.get_number_of_segments()):
                s = link.get_segment(j)
                roads.append((s.get_start_x(), s.get_start_y(),
                              s.get_end_x(), s.get_end_y(), s.get_segment_width()))
        if not roads:
            return
        for sx, sy, ex, ey, _w in roads:
            xs += [sx, ex]
            ys += [sy, ey]
        minx_m, maxx_m = min(xs), max(xs)
        miny_m, maxy_m = min(ys), max(ys)
        bbox_w = max(maxx_m - minx_m, 1.0)
        self.ppm = min(ppm, 1000.0 / bbox_w)
        self.TXv = margin - minx_m * self.ppm
        self.TYv = margin - miny_m * self.ppm
        self.W = int((maxx_m - minx_m) * self.ppm + 2 * margin)
        self.H = int((maxy_m - miny_m) * self.ppm + 2 * margin)
        self.pps = self.ppm * Parameters.strip_width
        self.ppfs = self.ppm * Parameters.footpath_strip_width

        # Static background: road bands then node markers and name labels.
        bg = []
        for sx, sy, ex, ey, w in roads:
            bg.append(
                f'<line x1="{self._tx(sx * self.ppm):.1f}" '
                f'y1="{self._ty(sy * self.ppm):.1f}" '
                f'x2="{self._tx(ex * self.ppm):.1f}" '
                f'y2="{self._ty(ey * self.ppm):.1f}" stroke="#9aa1ab" '
                f'stroke-width="{max(1.0, w * self.ppm):.1f}" stroke-linecap="round"/>')
        for node in processor.node_list:
            name = Parameters.NODE_NAMES.get(node.get_id(), str(node.get_id()))
            n = node.number_of_links()
            if n == 0:
                px, py = node.x, node.y
            else:
                sx = sy = 0.0
                for k in range(n):
                    lk = processor.link_list[node.get_link(k)]
                    if lk.get_up_node() == node.get_id():
                        seg = lk.get_first_segment()
                        sx += seg.get_start_x()
                        sy += seg.get_start_y()
                    else:
                        seg = lk.get_last_segment()
                        sx += seg.get_end_x()
                        sy += seg.get_end_y()
                px, py = sx / n, sy / n
            x = self._tx(px * self.ppm)
            y = self._ty(py * self.ppm)
            bg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#1b3a5b"/>'
                f'<text x="{x + 7:.1f}" y="{y - 6:.1f}" '
                f'font-family="Segoe UI,Arial,sans-serif" font-size="15" '
                f'font-weight="bold" fill="#12263a">{_esc(name)}</text>')
        self.bg = "".join(bg)

        end = max(1, Parameters.simulation_end_time)
        self.interval = max(1, end // max_frames)
        self.max_frames = max_frames
        self.ok = True

    def _tx(self, x):
        return x + self.TXv

    def _ty(self, y):
        return y + self.TYv

    def maybe_capture(self, step):
        if not self.ok or len(self.frames) >= self.max_frames:
            return
        if step % self.interval != 0:
            return
        g = _SVGGraphics(self._tx, self._ty)
        try:
            vehicles = self.proc.get_vehicle_list()
        except Exception:
            vehicles = []
        for v in vehicles:
            try:
                v.draw_vehicle(None, g, self.pps, self.ppm, self.ppfs)
            except Exception:
                pass
        caption = (f'<text x="12" y="22" font-family="Segoe UI,Arial,sans-serif" '
                   f'font-size="15" font-weight="bold" fill="#12263a">'
                   f'DhakaSim &#183; step {step}</text>')
        self.frames.append("".join(g.parts) + caption)

    def _svg(self, body, responsive=True):
        if responsive:
            attrs = 'style="width:100%;height:auto;display:block;background:#e9edf1"'
        else:
            attrs = f'width="{self.W}" height="{self.H}" style="background:#e9edf1"'
        return (f'<svg viewBox="0 0 {self.W} {self.H}" {attrs} '
                f'xmlns="http://www.w3.org/2000/svg">{self.bg}{body}</svg>')

    def finish(self):
        """Return ``{"animation": html, "snapshot": html}`` or ``None``."""
        if not self.ok or not self.frames:
            return None
        n = len(self.frames)
        snapshot = self._svg(self.frames[n // 2])
        dur = max(1.5, n * 0.18)
        film = "".join(f'<div class="dsf">{self._svg(fr)}</div>'
                       for fr in self.frames)
        animation = (
            "<style>"
            f".dswrap{{max-width:{self.W}px;overflow:hidden;border-radius:8px}}"
            f".dsfilm{{display:flex;width:{self.W * n}px;"
            f"animation:dsplay {dur:.1f}s steps({n}) infinite}}"
            f".dsf{{flex:0 0 {self.W}px}}"
            ".dsf svg{width:100%;height:auto;display:block}"
            "@keyframes dsplay{to{transform:translateX(-100%)}}"
            "</style>"
            f'<div class="dswrap"><div class="dsfilm">{film}</div></div>')
        return {"animation": animation, "snapshot": snapshot}
