"""Generates a self-contained HTML report after each simulation run.

No third-party dependencies -- plain string templating and the standard
library, matching the rest of DhakaSim.  The report explains every metric in
plain language so a first-time user can understand the output.

The report is written to ``statistics/report.html`` (overwritten each run).
The path of the most recent report is exposed as ``LAST_REPORT_PATH`` so the
GUI can open it when a run finishes.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone, timedelta
# Imported as a bare function: this module binds a local named
# `html` for the page it builds, which would shadow the module.
from html import escape as _escape

from .parameters import Parameters

LAST_REPORT_PATH = None

# DhakaSim's 13 vehicle types, in index order.
TYPE_NAMES = [
    "Bicycle", "Rickshaw", "Van / Cart", "Motorbike", "Car", "Car", "Car",
    "CNG / Auto", "Bus", "Bus", "Truck", "Truck", "Pedestrian (along road)",
]

# Which types are motorised (index >= 3, excluding the pedestrian type 12).
_NON_MOTORISED = {0, 1, 2}


#: How the option page names each SignalMode, so the report and the GUI agree.
_SIGNAL_MODE_NAMES = {
    "fixed": "Fixed time",
    "biased-random": "Biased random",
    "moo-v1": "Multi-objective v1 (Equations 1 and 2)",
    "moo-v2": "Multi-objective v2 (Equations 3 and 4)",
}


def _finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _num(v, decimals=1):
    """Format a number, or an em dash when it is NaN/inf/None."""
    if not _finite(v):
        return "&mdash;"
    return f"{float(v):,.{decimals}f}"


def _time_of_day_text(hour):
    """How the report names the demand window."""
    if hour is None or hour < 0:
        return "Peak hour (demand.txt as measured)"
    return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00 (hourly demand profile)"


def _weighted_mean(values, weights, index_filter):
    num = 0.0
    den = 0.0
    for i in index_filter:
        if i < len(values) and i < len(weights) and _finite(values[i]) and weights[i]:
            num += float(values[i]) * float(weights[i])
            den += float(weights[i])
    return (num / den) if den else float("nan")


def _svg_bar_chart(rows, unit="", decimals=0, label_w=120):
    """A dependency-free horizontal SVG bar chart.

    ``rows`` is a list of ``(label, value, colour_hex)`` tuples.
    """
    if not rows:
        return ""
    bar_w, row_h, gap, pad = 360, 24, 8, 12
    vmax = max((float(v) for _, v, _ in rows if _finite(v)), default=0) or 1
    width = label_w + bar_w + 74
    height = pad * 2 + len(rows) * (row_h + gap)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
    ]
    y = pad
    for label, value, color in rows:
        v = float(value) if _finite(value) else 0.0
        w = int(bar_w * v / vmax)
        cy = y + row_h / 2
        tip = f"{label}: {v:,.{decimals}f}{unit}"
        # Light ink: the chart sits on the report's dark card (gui._UI's
        # palette), where the old slate-blue labels disappear.
        parts.append(
            f'<text x="{label_w - 8}" y="{cy + 4:.0f}" text-anchor="end" '
            f'font-size="12" fill="#F4F1ED">{label}</text>'
            f'<rect x="{label_w}" y="{y}" width="{max(w, 2)}" height="{row_h}" '
            f'rx="4" fill="{color}" class="hov"><title>{tip}</title></rect>'
            f'<text x="{label_w + max(w, 2) + 6}" y="{cy + 4:.0f}" font-size="12" '
            f'fill="#C3BBB4">{v:,.{decimals}f}{unit}</text>'
        )
        y += row_h + gap
    parts.append("</svg>")
    return "".join(parts)


def _svg_pie_chart(rows, centre_label="vehicles"):
    """A donut chart with native hover tooltips.

    ``rows`` is ``(label, value, colour_hex)``.  Each slice carries an SVG
    ``<title>``, which every browser shows on hover -- the dependency-free
    tooltip.  Zero rows are kept in the side key but get no slice.
    """
    total = sum(float(v) for _, v, _ in rows if _finite(v) and v > 0)
    if total <= 0:
        return ""
    cx = cy = 128
    r, hole = 104, 60
    width, height = 470, 256
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif" class="pie">'
    ]
    angle = -math.pi / 2          # start at 12 o'clock, clockwise
    for label, value, color in rows:
        v = float(value) if _finite(value) else 0.0
        if v <= 0:
            continue
        frac = v / total
        tip = f"{label}: {v:,.0f} {centre_label} ({100 * frac:.1f}%)"
        if frac >= 0.9999:
            # A single non-zero row: two arcs cannot draw a full circle.
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{(r + hole) / 2}" fill="none" '
                f'stroke="{color}" stroke-width="{r - hole}" class="hov">'
                f'<title>{tip}</title></circle>')
            break
        a0, a1 = angle, angle + frac * 2 * math.pi
        angle = a1
        large = 1 if (a1 - a0) > math.pi else 0
        x0o, y0o = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1o, y1o = cx + r * math.cos(a1), cy + r * math.sin(a1)
        x0i, y0i = cx + hole * math.cos(a1), cy + hole * math.sin(a1)
        x1i, y1i = cx + hole * math.cos(a0), cy + hole * math.sin(a0)
        parts.append(
            f'<path d="M {x0o:.2f} {y0o:.2f} '
            f'A {r} {r} 0 {large} 1 {x1o:.2f} {y1o:.2f} '
            f'L {x0i:.2f} {y0i:.2f} '
            f'A {hole} {hole} 0 {large} 0 {x1i:.2f} {y1i:.2f} Z" '
            f'fill="{color}" stroke="#242120" stroke-width="1.5" class="hov">'
            f'<title>{tip}</title></path>')
        # A percentage on any slice wide enough to hold one.
        if frac >= 0.055:
            mid = (a0 + a1) / 2
            tr = (r + hole) / 2
            parts.append(
                f'<text x="{cx + tr * math.cos(mid):.1f}" '
                f'y="{cy + tr * math.sin(mid) + 4:.1f}" text-anchor="middle" '
                f'font-size="11" font-weight="600" fill="#131211" '
                f'pointer-events="none">{100 * frac:.0f}%</text>')
    parts.append(
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="22" '
        f'font-weight="700" fill="#F4F1ED">{total:,.0f}</text>'
        f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" font-size="11" '
        f'fill="#948B84">{centre_label}</text>')
    # Side key with the counts, so the numbers survive printing (no hover
    # on paper).
    ky = (height - len(rows) * 24) / 2 + 12
    for label, value, color in rows:
        v = float(value) if _finite(value) else 0.0
        parts.append(
            f'<rect x="262" y="{ky - 10:.0f}" width="12" height="12" rx="3" '
            f'fill="{color}"></rect>'
            f'<text x="282" y="{ky:.0f}" font-size="12" '
            f'fill="#F4F1ED">{label}</text>'
            f'<text x="{width - 12}" y="{ky:.0f}" font-size="12" '
            f'text-anchor="end" fill="#C3BBB4">{v:,.0f}</text>')
        ky += 24
    parts.append("</svg>")
    return "".join(parts)


def _svg_vbar_chart(rows, unit="", decimals=1):
    """A vertical SVG bar chart with hover tooltips; ``rows`` as above."""
    if not rows:
        return ""
    slot, gap, pad, plot_h, base = 52, 10, 14, 168, 210
    vmax = max((float(v) for _, v, _ in rows if _finite(v)), default=0) or 1
    width = pad * 2 + len(rows) * (slot + gap) - gap
    height = 252
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
    ]
    x = pad
    for label, value, color in rows:
        v = float(value) if _finite(value) else 0.0
        h = max(3, int(plot_h * v / vmax))
        tip = f"{label}: {v:,.{decimals}f}{unit}"
        parts.append(
            f'<text x="{x + slot / 2:.0f}" y="{base - h - 8}" '
            f'text-anchor="middle" font-size="11" '
            f'fill="#C3BBB4">{v:,.{decimals}f}</text>'
            f'<rect x="{x + 6}" y="{base - h}" width="{slot - 12}" '
            f'height="{h}" rx="4" fill="{color}" class="hov">'
            f'<title>{tip}</title></rect>'
            f'<text x="{x + slot / 2:.0f}" y="{base + 16}" text-anchor="middle" '
            f'font-size="9.5" fill="#F4F1ED">{label}</text>')
        x += slot + gap
    parts.append(f'<line x1="{pad}" y1="{base}" x2="{width - pad}" '
                 f'y2="{base}" stroke="#443F3C" stroke-width="1"/></svg>')
    return "".join(parts)


def _svg_line_chart(values, unit="", colour="#5BD986", x_label="minute"):
    """An area/line time-series chart with a hover tooltip per point."""
    pts = [(i, float(v)) for i, v in enumerate(values) if _finite(v)]
    if len(pts) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 46, 14, 14, 30
    width, height = 780, 220
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vmax = max(v for _, v in pts) or 1
    n = len(pts)

    def px(i):
        return pad_l + plot_w * i / max(n - 1, 1)

    def py(v):
        return pad_t + plot_h * (1 - v / vmax)

    line = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in pts)
    area = (f"{px(0):.1f},{pad_t + plot_h} " + line
            + f" {px(n - 1):.1f},{pad_t + plot_h}")
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
    ]
    # horizontal grid at 0, half and max
    for frac in (0.0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
            f'y2="{gy:.1f}" stroke="#443F3C" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#948B84">{vmax * frac:,.0f}</text>')
    parts.append(
        f'<polygon points="{area}" fill="{colour}" opacity="0.16"></polygon>'
        f'<polyline points="{line}" fill="none" stroke="{colour}" '
        f'stroke-width="2"></polyline>')
    for i, v in pts:
        parts.append(
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="7" fill="transparent" '
            f'class="hov"><title>{x_label} {i + 1}: {v:,.0f}{unit}</title>'
            f'</circle>')
    parts.append(
        f'<text x="{pad_l + plot_w / 2:.0f}" y="{height - 8}" '
        f'text-anchor="middle" font-size="11" fill="#948B84">'
        f'{x_label.capitalize()}s from the start of the run</text></svg>')
    return "".join(parts)


def _svg_scatter(points, x_label, y_label):
    """A scatter plot; ``points`` is ``(label, x, y, colour_hex)``."""
    pts = [(la, float(x), float(y), c) for la, x, y, c in points
           if _finite(x) and _finite(y)]
    if not pts:
        return ""
    pad_l, pad_r, pad_t, pad_b = 46, 16, 14, 40
    width, height = 470, 260
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    xmax = max(x for _, x, _, _ in pts) * 1.15 or 1
    ymax = max(y for _, _, y, _ in pts) * 1.15 or 1
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
    ]
    for frac in (0.0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        gx = pad_l + plot_w * frac
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
            f'y2="{gy:.1f}" stroke="#443F3C" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#948B84">{ymax * frac:,.0f}</text>'
            f'<text x="{gx:.1f}" y="{pad_t + plot_h + 14}" '
            f'text-anchor="middle" font-size="10" '
            f'fill="#948B84">{xmax * frac:,.0f}</text>')
    for label, x, y, colour in pts:
        sx = pad_l + plot_w * x / xmax
        sy = pad_t + plot_h * (1 - y / ymax)
        parts.append(
            f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="7" fill="{colour}" '
            f'stroke="#131211" stroke-width="1.5" class="hov">'
            f'<title>{label}: {x:,.1f} {x_label}, {y:,.1f} {y_label}</title>'
            f'</circle>')
    parts.append(
        f'<text x="{pad_l + plot_w / 2:.0f}" y="{height - 6}" '
        f'text-anchor="middle" font-size="11" fill="#948B84">{x_label}</text>'
        f'<text x="12" y="{pad_t + plot_h / 2:.0f}" text-anchor="middle" '
        f'font-size="11" fill="#948B84" transform="rotate(-90 12 '
        f'{pad_t + plot_h / 2:.0f})">{y_label}</text></svg>')
    return "".join(parts)


def write_html_report(params: dict, per_type: dict, totals, visual=None,
                      series=None) -> str:
    """Build the HTML report and return its path.

    ``per_type`` holds per-vehicle-type arrays (length 13): ``avg_speed`` in
    km/h, ``counts`` (vehicles seen), ``waiting_pct`` (% of travel time spent
    stopped), ``generated``, ``trips``, ``avg_tt`` (minutes), ``avg_fuel``
    (litres), ``collision`` and ``accident``.  ``totals`` is
    ``[collisions, accidents, vehicles_generated, pedestrians_generated]``.
    ``series`` (optional) feeds the dashboard: ``flow_per_min``,
    ``link_flow``, ``link_speed``, ``link_waiting`` and ``link_names``.
    """
    global LAST_REPORT_PATH
    series = series or {}

    speed = per_type["avg_speed"]
    counts = per_type["counts"]
    waiting = per_type["waiting_pct"]

    motorised = [i for i in range(12) if i not in _NON_MOTORISED]
    non_motorised = sorted(_NON_MOTORISED)
    everyone = list(range(12))

    overall_speed = _weighted_mean(speed, counts, everyone)
    mot_speed = _weighted_mean(speed, counts, motorised)
    non_speed = _weighted_mean(speed, counts, non_motorised)
    overall_wait = _weighted_mean(waiting, counts, everyone)
    mot_wait = _weighted_mean(waiting, counts, motorised)
    non_wait = _weighted_mean(waiting, counts, non_motorised)

    total_generated = int(sum(v for v in per_type["generated"][:12] if _finite(v)))
    total_trips = int(sum(v for v in per_type["trips"][:12] if _finite(v)))
    collisions = int(totals[0]) if _finite(totals[0]) else 0
    accidents = int(totals[1]) if _finite(totals[1]) else 0
    peds_generated = int(totals[3]) if len(totals) > 3 and _finite(totals[3]) else 0

    max_speed_kmh = params.get("maximum_speed", 0.0) * 3.6
    end_time = params.get("end_time", 0)
    minutes = end_time / 60.0 if end_time else 0

    # Per-type table rows (skip the pedestrian pseudo-type 12 in the vehicle
    # table; it is reported separately).
    rows = ""
    for i in range(12):
        rows += (
            "<tr>"
            f"<td class='t'>{TYPE_NAMES[i]}</td>"
            f"<td>{_num(per_type['generated'][i], 0)}</td>"
            f"<td>{_num(per_type['trips'][i], 0)}</td>"
            f"<td>{_num(speed[i], 1)}</td>"
            f"<td>{_num(waiting[i], 1)}</td>"
            f"<td>{_num(per_type['avg_tt'][i], 2)}</td>"
            f"<td>{_num(per_type['avg_fuel'][i], 3)}</td>"
            "</tr>\n"
        )

    def yn(b):
        return "On" if b else "Off"

    config_rows = [
        ("Random seed", params.get("seed"),
         "Fixes the random number generator so a run can be repeated exactly."),
        ("Simulation duration", f"{end_time} s ({minutes:.0f} min)",
         "How long the traffic was simulated, in seconds."),
        ("Car-following model", params.get("cf_model", "&mdash;"),
         "Rule that decides how each vehicle follows the one ahead."),
        ("Lane-changing model", params.get("dlc_model", "&mdash;"),
         "Rule for sideways (strip-to-strip) movement in non-lane traffic."),
        ("Strip width", f"{params.get('strip_width')} m",
         "Width of each lateral strip; vehicles occupy several strips."),
        ("Footpath strip width", f"{params.get('footpath_strip_width')} m",
         "Strip granularity on the footpath."),
        ("Network speed limit", f"{max_speed_kmh:.0f} km/h",
         "Upper speed bound applied across the network."),
        ("Signal control", _SIGNAL_MODE_NAMES.get(params.get("signal_mode"),
                                                  str(params.get("signal_mode"))),
         "How each junction decides how long an approach holds a green."),
        ("Signal change interval", f"{params.get('signal_change')} s",
         "Green each approach gets under fixed-time control; ignored by the "
         "scheduled modes, which set their own."),
        ("Road-crossing pedestrians", yn(params.get("across_ped")),
         "Whether pedestrians crossing the road are simulated."),
        ("Along-road pedestrians", yn(params.get("along_ped")),
         "Whether pedestrians walking along the road are simulated."),
        ("Roadside objects", yn(params.get("object_mode")),
         "Parked cars, rickshaws, CNGs and standing pedestrians (side friction)."),
        ("Network size",
         f"{params.get('num_nodes')} nodes, {params.get('num_links')} links, "
         f"{params.get('num_od')} O-D pairs",
         "The road network this run used."),
        ("Network folder", params.get("network") or "input/ (default)",
         "The input folder the run loaded its files from."),
        ("Time of day", _time_of_day_text(params.get("time_of_day", -1)),
         "Which hour's demand the run drew, where the network carries an "
         "hourly profile."),
        ("Real geometry", yn(params.get("geometry_mode")),
         "Medians, roundabouts, one-way links and turn lanes from "
         "geometry.txt; Off is the Java-parity network."),
        ("Junction discipline",
         "VISSIM-style (KeepClearMode On)" if params.get("keep_clear")
         else "Java parity (KeepClearMode Off)",
         "On holds queues at stop lines on the junction box edge and keeps "
         "the box clear; Off lets queues form on the junction itself."),
    ]
    config_html = "\n".join(
        f"<tr><td class='t'>{k}</td><td>{v}</td><td class='d'>{d}</td></tr>"
        for k, v, d in config_rows
    )

    glossary = [
        ("Average speed",
         "Mean speed of vehicles, in km/h. In heavy Dhaka traffic this is "
         "often below 10 km/h. Reported overall and split into motorised "
         "(car, motorbike, CNG, bus, truck) and non-motorised (bicycle, "
         "rickshaw, van/cart)."),
        ("Waiting time (%)",
         "Share of a vehicle's total travel time spent completely stopped. "
         "Higher means more congestion."),
        ("Vehicles generated",
         "How many vehicles entered the network during the run, per type."),
        ("Trips completed",
         "Vehicles that reached their destination before the run ended."),
        ("Average trip time",
         "Mean time from origin to destination for completed trips, in minutes."),
        ("Average fuel",
         "Mean fuel consumed per completed trip, in litres (estimated from "
         "the speed/acceleration profile)."),
        ("Motorised vs non-motorised",
         "Non-motorised = bicycle, rickshaw, van/cart. Everything else is "
         "motorised. Pedestrians are reported separately."),
        ("Collisions and accidents",
         "A collision is a vehicle-to-vehicle conflict; an accident involves a "
         "pedestrian. Note: in the current build these counters read 0 even "
         "when accidents occur — the per-event detail is written to "
         "statistics/csv/accident_log.csv and printed to the console instead."),
        ("Strip",
         "DhakaSim divides each road into thin lateral strips instead of lanes. "
         "A vehicle occupies several strips and can move to any free strip, "
         "which is how non-lane-based heterogeneous traffic is modelled."),
    ]
    glossary_html = "\n".join(
        f"<dt>{k}</dt><dd>{d}</dd>" for k, d in glossary
    )

    # Charts and colour legend, grouped into the eight primary categories
    # (cars, buses and trucks each cover a couple of type indices).
    from .constants import Constants
    colours = ["#%02x%02x%02x" % c for c in Constants.VEHICLE_TYPE_COLORS]
    groups = [
        ("Bicycle", [0]), ("Rickshaw", [1]), ("Van / Cart", [2]),
        ("Motorbike", [3]), ("Car", [4, 5, 6]), ("CNG / Auto", [7]),
        ("Bus", [8, 9]), ("Truck", [10, 11]),
    ]
    gen_rows, spd_rows, wait_rows, tt_rows = [], [], [], []
    scatter_pts, legend_rows = [], []
    for name, idxs in groups:
        colour = colours[idxs[0]]
        gen = sum(per_type["generated"][i] for i in idxs
                  if _finite(per_type["generated"][i]))
        spd = _weighted_mean(speed, counts, idxs)
        wai = _weighted_mean(waiting, counts, idxs)
        trt = _weighted_mean(per_type["avg_tt"], per_type["trips"], idxs)
        gen_rows.append((name, gen, colour))
        spd_rows.append((name, spd if _finite(spd) else 0.0, colour))
        wait_rows.append((name, wai if _finite(wai) else 0.0, colour))
        tt_rows.append((name, trt if _finite(trt) else 0.0, colour))
        if _finite(spd) and _finite(wai):
            scatter_pts.append((name, spd, wai, colour))
        legend_rows.append((name, colour, idxs))
    # A donut, not bars, for the composition: shares are what a modal split
    # is about, and the counts survive in the side key and on hover.
    gen_chart = _svg_pie_chart(gen_rows)
    spd_chart = _svg_vbar_chart(spd_rows, unit=" km/h", decimals=1)

    # ---- dashboard -------------------------------------------------------
    completion = (100.0 * total_trips / total_generated
                  if total_generated else float("nan"))
    flow_series = [v for v in (series.get("flow_per_min") or [])
                   if _finite(v)]
    peak_flow = max(flow_series) if flow_series else float("nan")
    mean_flow = (sum(flow_series) / len(flow_series)
                 if flow_series else float("nan"))
    avg_tt_all = _weighted_mean(per_type["avg_tt"], per_type["trips"],
                                everyone)
    avg_fuel_all = _weighted_mean(per_type["avg_fuel"], per_type["trips"],
                                  motorised)
    # The dashboard opens the report, so its tiles carry the headline speeds
    # too -- there is no separate card row any more.
    kpis = [
        ("Vehicles generated", f"{total_generated:,}", "entered the network"),
        ("Trips completed", f"{total_trips:,}", "reached destination"),
        ("Completion rate",
         (_num(completion, 1) + "%") if _finite(completion) else "&mdash;",
         "of generated vehicles"),
        ("Overall avg speed", _num(overall_speed, 1), "km/h, all vehicles"),
        ("Motorised avg speed", _num(mot_speed, 1), "km/h"),
        ("Non-motorised avg speed", _num(non_speed, 1), "km/h"),
        ("Time stopped",
         (_num(overall_wait, 1) + "%") if _finite(overall_wait) else "&mdash;",
         "share of travel time"),
        ("Avg trip time", _num(avg_tt_all, 1), "minutes, completed trips"),
        ("Peak flow", _num(peak_flow, 0), "veh/min across sensors"),
        ("Mean flow", _num(mean_flow, 0), "veh/min across sensors"),
        ("Avg fuel / trip", _num(avg_fuel_all, 2), "litres, motorised"),
    ]
    kpi_html = "\n".join(
        f'<div class="kpi"><div class="kv">{v}</div>'
        f'<div class="kk">{k}</div><div class="ku">{u}</div></div>'
        for k, v, u in kpis)

    flow_chart = _svg_line_chart(flow_series, unit=" vehicles")
    wait_chart = _svg_bar_chart(wait_rows, unit="%", decimals=1)
    tt_chart = _svg_vbar_chart(tt_rows, unit=" min", decimals=1)
    scatter_chart = _svg_scatter(scatter_pts, "average speed (km/h)",
                                 "time stopped (%)")

    # Busiest links, named where the network names them.
    link_flow = series.get("link_flow") or []
    link_names = series.get("link_names") or {}
    link_speed = series.get("link_speed") or []
    ranked = sorted((f, i) for i, f in enumerate(link_flow)
                    if _finite(f) and f > 0)[::-1][:8]
    link_rows, link_spd_rows = [], []
    for f, i in ranked:
        name = str(link_names.get(i, "")).strip() or f"Link {i}"
        if len(name) > 22:
            name = name[:21] + "…"
        name = _escape(name)
        link_rows.append((name, f, "#5BD986"))
        if i < len(link_speed) and _finite(link_speed[i]):
            link_spd_rows.append((name, link_speed[i], "#8AB8E8"))
    links_chart = _svg_bar_chart(link_rows, unit=" veh/h", decimals=0,
                                 label_w=170)
    links_spd_chart = _svg_bar_chart(link_spd_rows, unit=" km/h", decimals=1,
                                     label_w=170)

    def _panel(title, blurb, chart, wide=False):
        if not chart:
            return ""
        cls = "panel wide" if wide else "panel"
        return (f'<div class="{cls}"><h3>{title}</h3>'
                f'<p class="pd">{blurb}</p>{chart}</div>')

    dash_panels = "".join((
        _panel("Network flow over the run",
               "Vehicles crossing the links&rsquo; sensors, minute by minute "
               "&mdash; the run&rsquo;s demand actually arriving.",
               flow_chart, wide=True),
        _panel("Time stopped by type",
               "Share of each type&rsquo;s travel time spent standing still.",
               wait_chart),
        _panel("Average trip time by type",
               "Origin to destination, completed trips only.", tt_chart),
        _panel("Busiest links",
               "Flow measured at each link&rsquo;s sensor, top eight.",
               links_chart),
        _panel("Speed on the busiest links",
               "Mean speed on the same links &mdash; where demand meets "
               "congestion.", links_spd_chart),
        _panel("Speed against time stopped",
               "Each dot one vehicle type. The lower-right corner is "
               "free-flowing; the upper-left is congestion.", scatter_chart),
    ))
    dashboard_html = ""
    if kpi_html or dash_panels:
        dashboard_html = (
            '<h2>Network dashboard</h2>\n'
            '<p class="sub">The run at a glance. Hover any bar, slice or '
            'point for exact values. Every series shown here is also written '
            'to <code>statistics/csv/</code>, ready for ML pipelines and '
            'further analysis.</p>\n'
            f'<div class="kpis">{kpi_html}</div>\n'
            f'<div class="dash">{dash_panels}</div>')

    def _shades(idxs):
        if len(idxs) <= 1:
            return ""
        sw = "".join(
            f'<span style="width:12px;height:12px;border-radius:3px;'
            f'background:{colours[i]};display:inline-block;'
            f'border:1px solid #FFFFFF22"></span>' for i in idxs)
        return f'<span style="margin-left:6px">{sw}</span>'

    legend_html = '<div class="legend">' + "".join(
        f'<div class="lgi"><span class="sw" style="background:{colour}"></span>'
        f'<span>{name}{_shades(idxs)}</span></div>'
        for name, colour, idxs in legend_rows
    ) + "</div>"

    # Side friction has its own key.  These are obstructions rather than
    # traffic, and they are the one thing in the picture that is not in the
    # per-type tables, so without a key a reader has no way to name them.
    # Only when the run generated them: with ObjectMode Off (Miami, Riyadh,
    # or the start screen's Side friction switch) the key would describe
    # things that are not in the picture, so the whole section is dropped.
    if Parameters.OBJECT_MODE:
        friction_html = (
            '<h2>Side-friction colour legend</h2>'
            '<p class="sub">Parked vehicles and standing pedestrians block '
            'part of the carriageway without moving. Each type has its own '
            'pale hue, washed out beside the saturated moving palette so an '
            'obstruction is never read as traffic.</p>'
            '<div class="legend">' + "".join(
                f'<div class="lgi">'
                f'<span class="sw" style="background:{c.to_hex()}">'
                f'</span><span>{name}</span></div>'
                for name, c in (
                    ("Standing pedestrian",
                     Constants.STANDING_PEDESTRIAN_COLOR),
                    ("Parked car", Constants.PARKED_CAR_COLOR),
                    ("Parked rickshaw", Constants.PARKED_RICKSHAW_COLOR),
                    ("Parked CNG", Constants.PARKED_CNG_COLOR),
                )
            ) + "</div>")
    else:
        friction_html = ""

    # Bangladesh Standard Time (UTC+6, no daylight saving).
    now_bd = datetime.now(timezone.utc) + timedelta(hours=6)
    hour12 = now_bd.strftime("%I").lstrip("0") or "12"
    generated_at = (now_bd.strftime("%d %B %Y, ") + hour12
                    + now_bd.strftime(":%M %p") + " BDT")

    # Where the run happened.  Both spellings are prepared here because the
    # browser tab wants it appended and the page wants it on its own line.
    # Escaped because PlaceName can be set from the command line, so it is
    # not necessarily one of the strings shipped in place.txt.
    place = _escape(Parameters.PLACE_NAME or "")
    place_title = f" &mdash; {place}" if place else ""
    place_heading = f"Road network: {place}" if place else "Road network: unnamed"

    # Embedded run animations (self-contained SVG, produced during the run --
    # no third-party dependencies).  Two views of the same frames of the same
    # run: the plan view the operator watches, and the 3D view the GUI can be
    # switched to.
    viz_html = ""
    if visual and visual.get("animation"):
        credit = visual.get("basemap_credit")
        blurb = ('The network from above, each vehicle a rectangle in its '
                 'type colour.')
        if credit:
            blurb += (' Drawn over the aerial imagery the network was built '
                      'from, so the model can be checked against the real '
                      'junction.')
        viz_html += ('<h2>Run animation &mdash; plan view</h2>\n'
                     f'<p class="sub">{blurb}</p>\n<div class="viz">'
                     + visual["animation"] + "</div>\n")
        if credit:
            viz_html += f'<p class="sub">{_escape(credit)}</p>\n'
    if visual and visual.get("animation_3d"):
        viz_html += ('<h2>Run animation &mdash; 3D view</h2>\n'
                     '<p class="sub">The same frames of the same run through '
                     'the 3D camera, with each vehicle modelled to its real '
                     'size. Looking north.</p>\n<div class="viz">'
                     + visual["animation_3d"] + "</div>\n")
    if not viz_html:
        viz_html = ('<p class="sub">Run animation not available for this run '
                    '(a replay/trace run, or animation frames set to 0).</p>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DhakaSim Simulation Report{place_title}</title>
<style>
  /* The start screen's palette (gui._UI), so the report reads as a page of
     the same application: warm charcoal ground, panel cards, and the CNG
     green as the accent.  The animations keep their own daylight inside
     their frames -- they are the picture, not the chrome. */
  :root {{ --ink:#F4F1ED; --muted:#C3BBB4; --faint:#948B84; --line:#443F3C;
          --accent:#5BD986; --accent-strong:#1EA046;
          --bg:#131211; --card:#242120; --band:#302C29; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); background:var(--bg); margin:0; padding:32px; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  .place {{ margin:2px 0 14px; font-size:15px; font-weight:600;
           color:var(--accent); letter-spacing:.01em; }}
  /* Headings, blurbs and charts sit on the page's centre line; tables keep
     their own internal alignment but are centred as blocks. */
  h1, h2, .place, .sub {{ text-align:center; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  h2 {{ font-size:18px; margin:34px 0 12px; padding-bottom:6px;
        color:var(--accent); border-bottom:2px solid var(--line); }}
  .sub {{ color:var(--muted); margin:0 0 8px; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
           border:1px solid var(--line); border-radius:12px; overflow:hidden;
           font-size:14px; }}
  th, td {{ padding:9px 12px; text-align:right; border-bottom:1px solid var(--line); }}
  th {{ background:var(--band); font-weight:600; color:var(--muted); }}
  td.t, th:first-child {{ text-align:left; }}
  td.d {{ text-align:left; color:var(--muted); font-size:13px; }}
  tr:last-child td {{ border-bottom:none; }}
  dl {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:8px 18px; box-shadow:inset 0 1px 0 #524B47; }}
  dt {{ font-weight:700; margin-top:12px; }}
  dd {{ margin:4px 0 12px; color:var(--muted); }}
  .foot {{ color:var(--faint); font-size:12px; margin-top:28px; }}
  code {{ color:var(--accent); }}
  .chart {{ background:var(--card); border:1px solid var(--line);
            border-radius:12px; padding:14px 16px; margin-top:8px;
            box-shadow:inset 0 1px 0 #524B47; }}
  .viz {{ background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:10px; margin-top:8px;
          box-shadow:inset 0 1px 0 #524B47; }}
  .viz svg {{ max-width:100%; height:auto; border-radius:8px; }}
  .legend {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
             gap:10px 16px; background:var(--card); border:1px solid var(--line);
             border-radius:12px; padding:16px; font-size:14px;
             box-shadow:inset 0 1px 0 #524B47; }}
  .lgi {{ display:flex; align-items:center; gap:8px; }}
  .sw {{ width:16px; height:16px; border-radius:4px; flex:none;
         border:1px solid #FFFFFF22; }}
  .chart, .viz {{ text-align:center; }}
  /* Two charts abreast; they stack on a narrow window. */
  .duo {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(380px,1fr));
          gap:14px; margin-top:8px; }}
  /* Dashboard: a row of KPI tiles over a grid of chart panels. */
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px;
           margin-top:14px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:12px 8px; text-align:center;
          box-shadow:inset 0 1px 0 #524B47; }}
  .kv {{ font-size:22px; font-weight:700; color:var(--accent); }}
  .kk {{ font-size:12px; font-weight:600; margin-top:2px; }}
  .ku {{ font-size:11px; color:var(--faint); }}
  .dash {{ display:grid; grid-template-columns:1fr 1fr; gap:14px;
           margin-top:14px; }}
  .panel {{ background:var(--card); border:1px solid var(--line);
            border-radius:12px; padding:14px 16px; text-align:center;
            box-shadow:inset 0 1px 0 #524B47; }}
  .panel.wide {{ grid-column:1 / -1; }}
  .panel h3 {{ font-size:14px; margin:2px 0 4px; }}
  .pd {{ font-size:12px; color:var(--muted); margin:0 0 10px; }}
  /* Hover affordance for every mark that carries a tooltip. */
  .hov:hover {{ filter:brightness(1.25); }}
  .pie path.hov:hover, .pie circle.hov:hover {{ filter:brightness(1.12); }}
  @media (max-width:840px) {{
    .dash {{ grid-template-columns:1fr; }}
    .kpis {{ grid-template-columns:repeat(2,1fr); }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>DhakaSim &mdash; Simulation Report</h1>
  <p class="place">{place_heading}</p>
  <p class="sub">Generated {generated_at}. Microscopic simulation of
     non-lane-based, heterogeneous traffic.</p>

  {dashboard_html}

  <h2>What was simulated</h2>
  <table>
    <tr><th>Setting</th><th style="text-align:left">Value</th>
        <th style="text-align:left">Meaning</th></tr>
    {config_html}
  </table>

  <h2>Results by vehicle type</h2>
  <table>
    <tr><th>Type</th><th>Generated</th><th>Trips&nbsp;done</th>
        <th>Avg&nbsp;speed<br>(km/h)</th><th>Waiting<br>(%)</th>
        <th>Avg&nbsp;trip<br>(min)</th><th>Avg&nbsp;fuel<br>(L)</th></tr>
    {rows}
  </table>
  <p class="sub">Pedestrians generated during the run: {peds_generated:,}.
     Recorded collisions: {collisions}, accidents: {accidents}
     (see glossary).</p>

  <h2>Traffic composition and speed</h2>
  <div class="duo">
    <div class="panel"><h3>Vehicles generated by type</h3>
      <p class="pd">Hover a slice for the exact count; the key carries the
         numbers too.</p>{gen_chart}</div>
    <div class="panel"><h3>Average speed by type</h3>
      <p class="pd">km/h, weighted by the vehicles of each type seen.</p>
      {spd_chart}</div>
  </div>

  <h2>Vehicle colour legend</h2>
  <p class="sub">Each vehicle type is drawn in a fixed colour in the animation.
     Cars, buses and trucks span a few shades of the same hue (shown to the
     right of the label).</p>
  {legend_html}

  {friction_html}

  <h2>Glossary &mdash; what each term means</h2>
  <dl>{glossary_html}</dl>

  {viz_html}

  <p class="foot">DhakaSim &middot; report auto-generated at the end of the run.
     Full numeric outputs are in the <code>statistics/csv/</code> folder; per-event
     accident detail is in <code>statistics/csv/accident_log.csv</code>.</p>
</div>
</body>
</html>
"""

    os.makedirs(Parameters.STATS_DIR, exist_ok=True)
    # e.g. report_29July_2026_11.30.07PM_BDT.html  (colon is illegal in
    # Windows filenames, so the time uses dot separators).  The stamp
    # carries seconds, and a name that is still taken gets a counter: the
    # name used to stop at the minute, and a short run finishing in the
    # same minute as the one before it silently overwrote that report.
    stamp = (f"{now_bd.day}{now_bd.strftime('%B')}_{now_bd.year}_"
             f"{hour12}.{now_bd.strftime('%M.%S%p')}_BDT")
    path = os.path.join(Parameters.STATS_DIR, f"report_{stamp}.html")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(Parameters.STATS_DIR,
                            f"report_{stamp}_{counter}.html")
        counter += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    LAST_REPORT_PATH = os.path.abspath(path)
    return LAST_REPORT_PATH
