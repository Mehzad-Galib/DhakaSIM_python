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


def _weighted_mean(values, weights, index_filter):
    num = 0.0
    den = 0.0
    for i in index_filter:
        if i < len(values) and i < len(weights) and _finite(values[i]) and weights[i]:
            num += float(values[i]) * float(weights[i])
            den += float(weights[i])
    return (num / den) if den else float("nan")


def _svg_bar_chart(rows, unit="", decimals=0):
    """A dependency-free horizontal SVG bar chart.

    ``rows`` is a list of ``(label, value, colour_hex)`` tuples.
    """
    if not rows:
        return ""
    label_w, bar_w, row_h, gap, pad = 120, 360, 28, 9, 12
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
        # Light ink: the chart sits on the report's dark card (gui._UI's
        # palette), where the old slate-blue labels disappear.
        parts.append(
            f'<text x="{label_w - 8}" y="{cy + 4:.0f}" text-anchor="end" '
            f'font-size="13" fill="#F4F1ED">{label}</text>'
            f'<rect x="{label_w}" y="{y}" width="{max(w, 2)}" height="{row_h}" '
            f'rx="4" fill="{color}"></rect>'
            f'<text x="{label_w + max(w, 2) + 6}" y="{cy + 4:.0f}" font-size="12" '
            f'fill="#C3BBB4">{v:,.{decimals}f}{unit}</text>'
        )
        y += row_h + gap
    parts.append("</svg>")
    return "".join(parts)


def write_html_report(params: dict, per_type: dict, totals, visual=None) -> str:
    """Build the HTML report and return its path.

    ``per_type`` holds per-vehicle-type arrays (length 13): ``avg_speed`` in
    km/h, ``counts`` (vehicles seen), ``waiting_pct`` (% of travel time spent
    stopped), ``generated``, ``trips``, ``avg_tt`` (minutes), ``avg_fuel``
    (litres), ``collision`` and ``accident``.  ``totals`` is
    ``[collisions, accidents, vehicles_generated, pedestrians_generated]``.
    """
    global LAST_REPORT_PATH

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

    cards = [
        ("Overall average speed", _num(overall_speed, 1), "km/h"),
        ("Overall waiting time", _num(overall_wait, 1), "% of travel time stopped"),
        ("Motorised avg speed", _num(mot_speed, 1), "km/h"),
        ("Non-motorised avg speed", _num(non_speed, 1), "km/h"),
        ("Vehicles generated", f"{total_generated:,}", "over the run"),
        ("Trips completed", f"{total_trips:,}", "reached destination"),
    ]
    card_html = "\n".join(
        f'<div class="card"><div class="cv">{v}</div>'
        f'<div class="ck">{k}</div><div class="cu">{u}</div></div>'
        for k, v, u in cards
    )

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
    gen_rows, spd_rows, legend_rows = [], [], []
    for name, idxs in groups:
        colour = colours[idxs[0]]
        gen = sum(per_type["generated"][i] for i in idxs
                  if _finite(per_type["generated"][i]))
        spd = _weighted_mean(speed, counts, idxs)
        gen_rows.append((name, gen, colour))
        spd_rows.append((name, spd if _finite(spd) else 0.0, colour))
        legend_rows.append((name, colour, idxs))
    gen_chart = _svg_bar_chart(gen_rows, decimals=0)
    spd_chart = _svg_bar_chart(spd_rows, unit="", decimals=1)

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
    friction_html = '<div class="legend">' + "".join(
        f'<div class="lgi"><span class="sw" style="background:{c.to_hex()}">'
        f'</span><span>{name}</span></div>'
        for name, c in (
            ("Standing pedestrian", Constants.STANDING_PEDESTRIAN_COLOR),
            ("Parked car", Constants.PARKED_CAR_COLOR),
            ("Parked rickshaw", Constants.PARKED_RICKSHAW_COLOR),
            ("Parked CNG", Constants.PARKED_CNG_COLOR),
        )
    ) + "</div>"

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
  .wrap {{ max-width:960px; margin:0 auto; }}
  .place {{ margin:2px 0 14px; font-size:15px; font-weight:600;
           color:var(--accent); letter-spacing:.01em; }}
  h1 {{ font-size:26px; margin:0 0 4px; }}
  h2 {{ font-size:18px; margin:34px 0 12px; padding-bottom:6px;
        color:var(--accent); border-bottom:2px solid var(--line); }}
  .sub {{ color:var(--muted); margin:0 0 8px; font-size:14px; }}
  .cards {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px;
            margin-top:18px; }}
  .card {{ background:var(--card); border:1px solid var(--line);
           border-radius:12px; padding:16px;
           box-shadow:inset 0 1px 0 #524B47; }}
  .cv {{ font-size:28px; font-weight:700; color:var(--accent); }}
  .ck {{ font-size:13px; font-weight:600; margin-top:2px; }}
  .cu {{ font-size:12px; color:var(--faint); }}
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
</style>
</head>
<body>
<div class="wrap">
  <h1>DhakaSim &mdash; Simulation Report</h1>
  <p class="place">{place_heading}</p>
  <p class="sub">Generated {generated_at}. Microscopic simulation of
     non-lane-based, heterogeneous traffic.</p>

  <div class="cards">{card_html}</div>

  {viz_html}

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

  <h2>Vehicles generated by type</h2>
  <div class="chart">{gen_chart}</div>

  <h2>Average speed by type</h2>
  <div class="chart">{spd_chart}</div>

  <h2>Vehicle colour legend</h2>
  <p class="sub">Each vehicle type is drawn in a fixed colour in the animation.
     Cars, buses and trucks span a few shades of the same hue (shown to the
     right of the label).</p>
  {legend_html}

  <h2>Side-friction colour legend</h2>
  <p class="sub">Parked vehicles and standing pedestrians block part of the
     carriageway without moving. They share a yellow family so that they read
     as obstructions rather than as traffic.</p>
  {friction_html}

  <h2>Glossary &mdash; what each term means</h2>
  <dl>{glossary_html}</dl>

  <p class="foot">DhakaSim &middot; report auto-generated at the end of the run.
     Full numeric outputs are in the <code>statistics/csv/</code> folder; per-event
     accident detail is in <code>statistics/csv/accident_log.csv</code>.</p>
</div>
</body>
</html>
"""

    os.makedirs(Parameters.STATS_DIR, exist_ok=True)
    # e.g. report_29July_2026_11.30PM_BDT.html  (colon is illegal in Windows
    # filenames, so the time uses a dot separator).
    stamp = (f"{now_bd.day}{now_bd.strftime('%B')}_{now_bd.year}_"
             f"{hour12}.{now_bd.strftime('%M%p')}_BDT")
    path = os.path.join(Parameters.STATS_DIR, f"report_{stamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    LAST_REPORT_PATH = os.path.abspath(path)
    return LAST_REPORT_PATH
