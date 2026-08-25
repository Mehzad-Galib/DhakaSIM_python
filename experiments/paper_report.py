#!/usr/bin/env python3
"""Build the HTML report comparing our sweep against the RoadBird paper.

Reads ``results.csv`` from :mod:`roadbird` and writes one self-contained HTML
file: what was run, what came out, and how each of the paper's stated claims
about Dhaka fares against our numbers.

The paper reports its Dhaka results as per-link bar charts (its Figures 10, 11
and 12) with a dashed line for the overall average.  We reproduce that shape --
a per-link chart plus the average -- so the two can be read side by side.

Every claim tested here is quoted from the paper's prose rather than read off a
bar chart, because a value eyeballed from a figure is not evidence and would
make a comparison look more precise than it is.  Where the paper gives only a
direction ("higher than", "less than"), the test is a direction too.

Standard library only, and the charts are hand-built SVG, in keeping with the
rest of the project::

    python experiments/paper_report.py experiments/results/results.csv \
        --out experiments/paper_comparison.html
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stats import t_test_ind, ks_test_2samp  # noqa: E402
# Imported rather than copied: the column order of avg_speed_vehicle.csv is
# this list, and a local copy would drift the moment a type is added.
from dhakasim.report import TYPE_NAMES  # noqa: E402

# Bicycle, rickshaw and van/cart are the human-powered ones; index 12 is the
# along-road pedestrian, which is not a vehicle and is left out of both means.
NON_MOTORISED = (0, 1, 2)
PEDESTRIAN_TYPE = 12
MOTORISED = tuple(i for i in range(len(TYPE_NAMES))
                  if i not in NON_MOTORISED and i != PEDESTRIAN_TYPE)

# The harness writes the numeric rate into results.csv, not the label it was
# selected by, so the labels are recovered by sorting the distinct rates.  They
# are deliberately *not* hard-coded: `roadbird --rates` changes what low, medium
# and high mean, and a fixed table would silently mislabel a recalibrated sweep.
DEMAND_ORDER = ["low", "medium", "high"]
LANE_ORDER = ["non_lane", "lane"]

# The paper's Dhaka figures (10a-c, 11a-b, 12a-b) hold the vehicle mix at
# Dhaka's own heterogeneous split with pedestrians present, and vary only the
# lane system and the generation rate.  Pooling the homogeneous runs into those
# means would compare a different city's traffic, so the lane-versus-non-lane
# claims are tested on this slice alone; the mix and pedestrian claims below
# then vary exactly one factor away from it.
PRIMARY_MIX = "heterogeneous"
PRIMARY_PEDS = "peds"

# How each metric is displayed.  `better` records which direction is an
# improvement, so a verdict can say "non-lane wins" without the reader having to
# remember that low waiting time is good and low speed is not.
METRIC_INFO = {
    "link_avg_speed": ("Average speed on a link", "km/h", "high"),
    "link_avg_waiting": ("Average waiting time on a link", "s", "low"),
    "link_flow": ("Average vehicle flow rate on a link", "veh/h", "high"),
    "avg_speed_vehicle": ("Average speed by vehicle type", "km/h", "high"),
    "route_avg_tt_car": ("Average route travel time, car", "s", "low"),
    "route_avg_tt_motorbike": ("Average route travel time, motorbike", "s", "low"),
}

PALETTE = {"non_lane": "#2e7d32", "lane": "#c62828"}


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def load(path):
    """results.csv as a list of dicts, with non-finite values dropped.

    The simulator emits NaN for an average nobody contributed to -- a route no
    vehicle completed, say.  Those rows carry no information about the road and
    would drag any mean they entered, so they are dropped here rather than
    guarded against at every use.
    """
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Ascending rate becomes low/medium/high.  With fewer than three distinct
    # rates the labels would be guesses, so the rate itself is used instead.
    rates = sorted({r["demand"] for r in rows}, key=lambda v: float(v))
    labels = (dict(zip(rates, DEMAND_ORDER)) if len(rates) == 3
              else {rate: rate for rate in rates})

    kept = []
    for row in rows:
        try:
            value = float(row["value"])
        except (ValueError, KeyError):
            continue
        if value != value or math.isinf(value):
            continue
        row["value"] = value
        row["demand_label"] = labels[row["demand"]]
        row["seed"] = int(row["seed"])
        row["index"] = int(row["index"])
        kept.append(row)
    return kept


def demand_rates(rows):
    """label -> the veh/h per OD pair it stands for, for the scope note."""
    out = {}
    for row in rows:
        out[row["demand_label"]] = row["demand"]
    return out


def demand_levels(rows):
    """Demand labels present, ordered by the rate behind them.

    Derived from the data rather than from DEMAND_ORDER: a sweep that ran only
    two rates, or was cut short, labels its cells by rate, and iterating the
    fixed low/medium/high list would then match nothing and empty every chart.
    """
    seen = {}
    for row in rows:
        seen[row["demand_label"]] = float(row["demand"])
    return [label for label, _ in sorted(seen.items(), key=lambda kv: kv[1])]


def select(rows, metric=None, lane_mode=None, demand=None, mix=None, peds=None):
    out = rows
    if metric is not None:
        out = [r for r in out if r["metric"] == metric]
    if lane_mode is not None:
        out = [r for r in out if r["lane_mode"] == lane_mode]
    if demand is not None:
        out = [r for r in out if r["demand_label"] == demand]
    if mix is not None:
        out = [r for r in out if r["mix"] == mix]
    if peds is not None:
        out = [r for r in out if r["pedestrians"] == peds]
    return out


def primary(rows):
    """Dhaka's own condition: heterogeneous mix, pedestrians present.

    Falls back to whatever is present when a sweep did not vary these, so a
    narrower run still reports rather than silently emptying every chart.
    """
    subset = select(rows, mix=PRIMARY_MIX, peds=PRIMARY_PEDS)
    return subset if subset else rows


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else float("nan")


def stdev(values):
    values = list(values)
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def per_seed_means(rows):
    """One number per seed: the run's average over links (or vehicle types).

    The seed is the unit of replication -- links within a run share the same
    traffic and are not independent -- so the spread across seeds is the honest
    error bar, and it is what the charts draw.
    """
    by_seed = {}
    for row in rows:
        by_seed.setdefault(row["seed"], []).append(row["value"])
    return [mean(v) for _, v in sorted(by_seed.items())]


def cell(rows, metric, lane_mode, demand):
    """Summary of one (metric, lane mode, demand) cell."""
    subset = select(rows, metric, lane_mode, demand)
    seeds = per_seed_means(subset)
    return {
        "mean": mean(seeds) if seeds else float("nan"),
        "sd": stdev(seeds),
        "n_seeds": len(seeds),
        "values": [r["value"] for r in subset],   # per link, pooled over seeds
    }


# --------------------------------------------------------------------------
# SVG charts
# --------------------------------------------------------------------------

def _nice_top(value):
    """A round number at or above `value`, for an axis that ends somewhere sane."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if step * base >= value:
            return step * base
    return 10 * base


def _esc(text):
    return html.escape(str(text))


def grouped_bars(rows, metric, title):
    """Lane against non-lane across the three demand levels, with seed spread."""
    unit = METRIC_INFO[metric][1]
    demands = [d for d in demand_levels(rows)
               if select(rows, metric, demand=d)]
    if not demands:
        return "<p class='missing'>no data for this metric</p>"

    cells = {(lane, d): cell(rows, metric, lane, d)
             for lane in LANE_ORDER for d in demands}
    top = _nice_top(max((c["mean"] + c["sd"]) for c in cells.values()
                        if c["mean"] == c["mean"]))

    width, height = 620, 300
    left, bottom, top_pad = 62, 48, 22
    plot_w = width - left - 18
    plot_h = height - bottom - top_pad
    group_w = plot_w / len(demands)
    bar_w = min(56, group_w / 3.2)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
             f'role="img" aria-label="{_esc(title)}">']
    # gridlines and y axis
    for i in range(5):
        y = top_pad + plot_h * i / 4
        value = top * (1 - i / 4)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
                     f'y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="ytick">'
                     f'{value:.3g}</text>')
    parts.append(f'<line x1="{left}" y1="{top_pad}" x2="{left}" '
                 f'y2="{top_pad + plot_h}" class="axis"/>')
    parts.append(f'<line x1="{left}" y1="{top_pad + plot_h}" '
                 f'x2="{left + plot_w}" y2="{top_pad + plot_h}" class="axis"/>')

    for gi, demand in enumerate(demands):
        cx = left + group_w * (gi + 0.5)
        for bi, lane in enumerate(LANE_ORDER):
            c = cells[(lane, demand)]
            if c["mean"] != c["mean"]:
                continue
            x = cx + (bi - 1) * bar_w + bar_w * 0.1
            h = plot_h * c["mean"] / top
            y = top_pad + plot_h - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                         f'height="{h:.1f}" fill="{PALETTE[lane]}">'
                         f'<title>{_esc(lane)} / {_esc(demand)}: '
                         f'{c["mean"]:.4g} {_esc(unit)} '
                         f'(sd {c["sd"]:.3g}, {c["n_seeds"]} seeds)</title></rect>')
            # seed spread, drawn only when more than one seed contributed
            if c["n_seeds"] > 1 and c["sd"] > 0:
                ex = x + bar_w / 2
                hi = top_pad + plot_h - plot_h * (c["mean"] + c["sd"]) / top
                lo = top_pad + plot_h - plot_h * max(0.0, c["mean"] - c["sd"]) / top
                parts.append(f'<line x1="{ex:.1f}" y1="{hi:.1f}" x2="{ex:.1f}" '
                             f'y2="{lo:.1f}" class="err"/>')
                parts.append(f'<line x1="{ex - 5:.1f}" y1="{hi:.1f}" '
                             f'x2="{ex + 5:.1f}" y2="{hi:.1f}" class="err"/>')
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" '
                         f'class="barlabel">{c["mean"]:.3g}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{top_pad + plot_h + 20}" '
                     f'class="xtick">{_esc(demand)}</text>')

    parts.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 8}" '
                 f'class="axislabel">vehicle generation rate</text>')
    parts.append(f'<text x="14" y="{top_pad + plot_h / 2:.1f}" '
                 f'class="axislabel" transform="rotate(-90 14 '
                 f'{top_pad + plot_h / 2:.1f})">{_esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def per_link_bars(rows, metric, demand, title):
    """Per-link chart in the shape of the paper's Figures 10-12.

    Averaged over seeds so one bar is one link, with the dashed overall average
    the paper draws on the same axes.
    """
    unit = METRIC_INFO[metric][1]
    subset = select(rows, metric, demand=demand)
    if not subset:
        return "<p class='missing'>no data for this metric</p>"

    links = sorted({r["index"] for r in subset})
    series = {}
    for lane in LANE_ORDER:
        per_link = []
        for link in links:
            vals = [r["value"] for r in select(rows, metric, lane, demand)
                    if r["index"] == link]
            per_link.append(mean(vals) if vals else float("nan"))
        series[lane] = per_link
    finite = [v for lane in LANE_ORDER for v in series[lane] if v == v]
    if not finite:
        return "<p class='missing'>no data for this metric</p>"
    top = _nice_top(max(finite))

    width, height = 860, 300
    left, bottom, top_pad = 62, 48, 22
    plot_w = width - left - 18
    plot_h = height - bottom - top_pad
    slot = plot_w / max(1, len(links))
    bar_w = max(2.0, min(11.0, slot / 2.4))

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
             f'role="img" aria-label="{_esc(title)}">']
    for i in range(5):
        y = top_pad + plot_h * i / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
                     f'y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="ytick">'
                     f'{top * (1 - i / 4):.3g}</text>')

    for li, link in enumerate(links):
        cx = left + slot * (li + 0.5)
        for bi, lane in enumerate(LANE_ORDER):
            value = series[lane][li]
            if value != value:
                continue
            x = cx + (bi - 1) * bar_w
            h = plot_h * value / top
            parts.append(f'<rect x="{x:.1f}" y="{top_pad + plot_h - h:.1f}" '
                         f'width="{bar_w:.1f}" height="{h:.1f}" '
                         f'fill="{PALETTE[lane]}"><title>link {link} '
                         f'{_esc(lane)}: {value:.4g} {_esc(unit)}</title></rect>')
        if len(links) <= 30 or li % 2 == 0:
            parts.append(f'<text x="{cx:.1f}" y="{top_pad + plot_h + 15}" '
                         f'class="xtick small">{link}</text>')

    # the paper draws each system's overall average as a dashed line
    for lane in LANE_ORDER:
        vals = [v for v in series[lane] if v == v]
        if not vals:
            continue
        avg = mean(vals)
        y = top_pad + plot_h - plot_h * avg / top
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
                     f'y2="{y:.1f}" stroke="{PALETTE[lane]}" '
                     f'stroke-dasharray="6 4" stroke-width="1.6" opacity="0.95"/>')
        parts.append(f'<text x="{left + plot_w - 4}" y="{y - 5:.1f}" '
                     f'class="avglabel" text-anchor="end" '
                     f'fill="{PALETTE[lane]}">avg {avg:.4g}</text>')

    parts.append(f'<line x1="{left}" y1="{top_pad + plot_h}" '
                 f'x2="{left + plot_w}" y2="{top_pad + plot_h}" class="axis"/>')
    parts.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 8}" '
                 f'class="axislabel">link id</text>')
    parts.append(f'<text x="14" y="{top_pad + plot_h / 2:.1f}" class="axislabel" '
                 f'transform="rotate(-90 14 {top_pad + plot_h / 2:.1f})">'
                 f'{_esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def type_speed(rows, lane_mode, demand, indices):
    """Mean speed over a set of vehicle types, in the shape of the paper's
    motorised / non-motorised split."""
    subset = [r for r in select(rows, "avg_speed_vehicle", lane_mode, demand)
              if r["index"] in indices]
    return mean(per_seed_means(subset)) if subset else float("nan")


def per_type_bars(rows, demand, title):
    """Average speed by vehicle type -- the paper's Figs. 12e and 12f.

    Types nobody generated are dropped rather than drawn at zero, which would
    read as "this vehicle crawled" instead of "this vehicle was not there".
    """
    subset = select(rows, "avg_speed_vehicle", demand=demand)
    if not subset:
        return "<p class='missing'>no data for this metric</p>"

    indices = sorted({r["index"] for r in subset if r["index"] != PEDESTRIAN_TYPE})
    series = {lane: [mean(per_seed_means(
                        [r for r in select(rows, "avg_speed_vehicle", lane, demand)
                         if r["index"] == i])) for i in indices]
              for lane in LANE_ORDER}
    keep = [k for k, i in enumerate(indices)
            if any(series[lane][k] == series[lane][k] and series[lane][k] > 0
                   for lane in LANE_ORDER)]
    if not keep:
        return "<p class='missing'>no vehicle types were generated</p>"
    indices = [indices[k] for k in keep]
    series = {lane: [series[lane][k] for k in keep] for lane in LANE_ORDER}

    finite = [v for lane in LANE_ORDER for v in series[lane] if v == v]
    top = _nice_top(max(finite))

    width, height = 860, 300
    left, bottom, top_pad = 62, 60, 22
    plot_w = width - left - 18
    plot_h = height - bottom - top_pad
    slot = plot_w / len(indices)
    bar_w = min(26.0, slot / 2.4)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
             f'role="img" aria-label="{_esc(title)}">']
    for i in range(5):
        y = top_pad + plot_h * i / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" '
                     f'y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="ytick">'
                     f'{top * (1 - i / 4):.3g}</text>')

    for k, type_index in enumerate(indices):
        cx = left + slot * (k + 0.5)
        for bi, lane in enumerate(LANE_ORDER):
            value = series[lane][k]
            if value != value:
                continue
            x = cx + (bi - 1) * bar_w
            h = plot_h * value / top
            parts.append(f'<rect x="{x:.1f}" y="{top_pad + plot_h - h:.1f}" '
                         f'width="{bar_w:.1f}" height="{h:.1f}" '
                         f'fill="{PALETTE[lane]}"><title>'
                         f'{_esc(TYPE_NAMES[type_index])} {_esc(lane)}: '
                         f'{value:.4g} km/h</title></rect>')
        label = TYPE_NAMES[type_index] if type_index < len(TYPE_NAMES) else type_index
        parts.append(f'<text x="{cx:.1f}" y="{top_pad + plot_h + 16}" '
                     f'class="xtick small" transform="rotate(-30 {cx:.1f} '
                     f'{top_pad + plot_h + 16})">{_esc(label)}</text>')

    parts.append(f'<line x1="{left}" y1="{top_pad + plot_h}" '
                 f'x2="{left + plot_w}" y2="{top_pad + plot_h}" class="axis"/>')
    parts.append(f'<text x="14" y="{top_pad + plot_h / 2:.1f}" class="axislabel" '
                 f'transform="rotate(-90 14 {top_pad + plot_h / 2:.1f})">km/h</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend():
    items = "".join(
        f'<span class="key"><i style="background:{PALETTE[lane]}"></i>'
        f'{_esc(lane.replace("_", "-"))}</span>' for lane in LANE_ORDER)
    return f'<div class="legend">{items}</div>'


# --------------------------------------------------------------------------
# the paper's claims
# --------------------------------------------------------------------------

def direction(rows, metric, demand):
    """Compare non-lane against lane in one cell, with a two-sample test.

    The test runs over per-link values pooled across seeds, which is the unit
    the paper's own figures plot.  Links within a run are not independent, so
    the p-value is optimistic; it is reported as a guide to size, not as proof.
    """
    a = cell(rows, metric, "non_lane", demand)
    b = cell(rows, metric, "lane", demand)
    if not a["values"] or not b["values"]:
        return None
    out = {"non_lane": a["mean"], "lane": b["mean"],
           "n_non_lane": a["n_seeds"], "n_lane": b["n_seeds"]}
    if len(a["values"]) >= 2 and len(b["values"]) >= 2:
        _, p_t, _ = t_test_ind(a["values"], b["values"])
        _, p_ks = ks_test_2samp(a["values"], b["values"])
        out["p_t"], out["p_ks"] = p_t, p_ks
    if a["mean"] == a["mean"] and b["mean"] == b["mean"]:
        out["delta_pct"] = ((a["mean"] - b["mean"]) / b["mean"] * 100.0
                            if b["mean"] else float("nan"))
    return out


def trend(rows, metric, lane_mode):
    """The value at each demand level, for testing monotone claims."""
    got = []
    for demand in demand_levels(rows):
        c = cell(rows, metric, lane_mode, demand)
        if c["mean"] == c["mean"]:
            got.append((demand, c["mean"]))
    return got


def verdict_row(label, ok, detail):
    state = {True: ("supported", "ok"), False: ("not reproduced", "bad"),
             None: ("no data", "na")}[ok]
    return (f'<tr><td>{label}</td>'
            f'<td><span class="pill {state[1]}">{state[0]}</span></td>'
            f'<td>{detail}</td></tr>')


def build_claims(all_rows):
    """Each of the paper's Dhaka claims, tested against our numbers.

    The lane-versus-non-lane claims run on the primary slice; the last two vary
    the mix and the pedestrians away from it, one factor at a time.
    """
    rows = primary(all_rows)
    out = []

    # --- claim 1 -------------------------------------------------------
    # "irrespective of generation rate, the average speed for the non-lane road
    #  network is higher than that of lane-based road networks"
    details, results = [], []
    for demand in demand_levels(rows):
        d = direction(rows, "link_avg_speed", demand)
        if d is None:
            continue
        results.append(d["non_lane"] > d["lane"])
        details.append(f"{demand}: non-lane {d['non_lane']:.3g} vs lane "
                       f"{d['lane']:.3g} km/h ({d['delta_pct']:+.1f}%)")
    out.append(("Non-lane link speed exceeds lane speed at every generation rate",
                "Sec. VI-A-1",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 2 -------------------------------------------------------
    # "the average speed on links is below 10 km/hour, which matches the World
    #  Bank report, i.e., the average speed of Dhaka city is around 7 km/hour"
    speeds = [c for demand in demand_levels(rows) for lane in LANE_ORDER
              for c in [cell(rows, "link_avg_speed", lane, demand)["mean"]]
              if c == c]
    ok = all(s < 10.0 for s in speeds) if speeds else None
    # The 7 km/h the World Bank quotes for Dhaka is a city-wide average over a
    # fleet that is mostly rickshaws, so the split is reported beside the link
    # average -- the two answer different questions and diverge here.
    slow = [v for demand in demand_levels(rows) for lane in LANE_ORDER
            for v in [type_speed(rows, lane, demand, NON_MOTORISED)] if v == v]
    fast = [v for demand in demand_levels(rows) for lane in LANE_ORDER
            for v in [type_speed(rows, lane, demand, MOTORISED)] if v == v]
    split = ""
    if slow and fast:
        split = (f"; non-motorised {mean(slow):.3g} km/h, "
                 f"motorised {mean(fast):.3g} km/h")
    out.append(("Average link speed stays below 10 km/h (World Bank: ~7 km/h)",
                "Sec. VI-A-1",
                ok,
                (f"observed range {min(speeds):.3g}-{max(speeds):.3g} km/h "
                 f"across {len(speeds)} cells{split}") if speeds else "no cells"))

    # --- claim 3 -------------------------------------------------------
    # "speed on a link decreases with an increasing vehicle generation rate"
    details, results = [], []
    for lane in LANE_ORDER:
        series = trend(rows, "link_avg_speed", lane)
        if len(series) >= 2:
            results.append(all(series[i][1] >= series[i + 1][1]
                               for i in range(len(series) - 1)))
            details.append(f"{lane.replace('_', '-')}: " +
                           " > ".join(f"{v:.3g}" for _, v in series))
    out.append(("Link speed falls as the generation rate rises",
                "Sec. VI-A-1",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 4 -------------------------------------------------------
    # "the average non-lane-based waiting time is less than lane-based waiting
    #  time"
    details, results = [], []
    for demand in demand_levels(rows):
        d = direction(rows, "link_avg_waiting", demand)
        if d is None:
            continue
        results.append(d["non_lane"] < d["lane"])
        details.append(f"{demand}: non-lane {d['non_lane']:.4g} vs lane "
                       f"{d['lane']:.4g} s ({d['delta_pct']:+.1f}%)")
    out.append(("Non-lane waiting time is lower than lane waiting time",
                "Sec. VI-A-2",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 5 -------------------------------------------------------
    # "the average waiting time increases with the expansion in vehicle
    #  generation rate"
    details, results = [], []
    for lane in LANE_ORDER:
        series = trend(rows, "link_avg_waiting", lane)
        if len(series) >= 2:
            results.append(all(series[i][1] <= series[i + 1][1]
                               for i in range(len(series) - 1)))
            details.append(f"{lane.replace('_', '-')}: " +
                           " < ".join(f"{v:.4g}" for _, v in series))
    out.append(("Waiting time rises as the generation rate rises",
                "Sec. VI-A-2",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 6 -------------------------------------------------------
    # "the average vehicle flow rate increases with an increasing generation
    #  rate"
    details, results = [], []
    for lane in LANE_ORDER:
        series = trend(rows, "link_flow", lane)
        if len(series) >= 2:
            results.append(all(series[i][1] <= series[i + 1][1]
                               for i in range(len(series) - 1)))
            details.append(f"{lane.replace('_', '-')}: " +
                           " < ".join(f"{v:.4g}" for _, v in series))
    out.append(("Link flow rises as the generation rate rises",
                "Sec. VI-A-3",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 7 -------------------------------------------------------
    # Figures 13a and 14a put Dhaka's non-lane flow above its lane flow.
    details, results = [], []
    for demand in demand_levels(rows):
        d = direction(rows, "link_flow", demand)
        if d is None:
            continue
        results.append(d["non_lane"] > d["lane"])
        details.append(f"{demand}: non-lane {d['non_lane']:.4g} vs lane "
                       f"{d['lane']:.4g} veh/h ({d['delta_pct']:+.1f}%)")
    out.append(("Non-lane carries more flow than lane on Dhaka's network",
                "Figs. 13a, 14a",
                all(results) if results else None,
                "; ".join(details) or "no cells"))

    # --- claim 8 -------------------------------------------------------
    # "although non-lane-based networks perform better at a low generation
    #  rate, lane-based networks outperform non-lane-based networks at a high
    #  generation rate" -- the one crossover the paper predicts, and the only
    #  claim here whose two halves point opposite ways.
    low = direction(rows, "avg_speed_vehicle", "low")
    high = direction(rows, "avg_speed_vehicle", "high")
    if low is None or high is None:
        ok, detail = None, "needs both the low and high cells"
    else:
        ok = low["non_lane"] > low["lane"] and high["lane"] > high["non_lane"]
        detail = (f"low: non-lane {low['non_lane']:.3g} vs lane "
                  f"{low['lane']:.3g} km/h; high: non-lane "
                  f"{high['non_lane']:.3g} vs lane {high['lane']:.3g} km/h")
    out.append(("Vehicle speed: non-lane wins at low demand, lane wins at high "
                "(crossover)", "Sec. VI-A-4", ok, detail))

    # --- claim 9 -------------------------------------------------------
    # "Performance significantly improves for lane and non-lane-based traffic
    #  as slow vehicles are removed from the traffic stream"  (Figs. 10e, 10f)
    details, results = [], []
    for lane in LANE_ORDER:
        het = select(all_rows, "link_avg_speed", lane, mix="heterogeneous",
                     peds=PRIMARY_PEDS)
        hom = select(all_rows, "link_avg_speed", lane, mix="homogeneous",
                     peds=PRIMARY_PEDS)
        if not het or not hom:
            continue
        a, b = mean(per_seed_means(het)), mean(per_seed_means(hom))
        results.append(b > a)
        details.append(f"{lane.replace('_', '-')}: heterogeneous {a:.3g} vs "
                       f"homogeneous {b:.3g} km/h ({(b - a) / a * 100:+.1f}%)")
    out.append(("Removing slow vehicles (homogeneous mix) raises link speed for "
                "both systems", "Sec. VI-A-1, Figs. 10e-f",
                all(results) if results else None,
                "; ".join(details) or "needs both mixes"))

    # --- claim 10 ------------------------------------------------------
    # "Irregular road crossings by pedestrians do not significantly alter the
    #  performance of heterogeneous traffic streams but slightly decrease the
    #  performance of homogeneous ones."  Two halves, so both are reported and
    #  the verdict needs both: heterogeneous unmoved, homogeneous nudged down.
    details, halves = [], {}
    for mix in ("heterogeneous", "homogeneous"):
        on = select(all_rows, "link_avg_speed", mix=mix, peds="peds")
        off = select(all_rows, "link_avg_speed", mix=mix, peds="no_peds")
        if not on or not off:
            continue
        a, b = mean(per_seed_means(on)), mean(per_seed_means(off))
        shift = (a - b) / b * 100.0 if b else float("nan")
        halves[mix] = shift
        details.append(f"{mix}: {b:.3g} without vs {a:.3g} with pedestrians "
                       f"({shift:+.1f}%)")
    if len(halves) == 2:
        # "not significantly" read as within 5%, "slightly decrease" as a drop
        ok = abs(halves["heterogeneous"]) < 5.0 and halves["homogeneous"] < 0.0
    else:
        ok = None
    out.append(("Pedestrians barely affect heterogeneous traffic but slow "
                "homogeneous traffic", "Sec. VII", ok,
                "; ".join(details) or "needs both pedestrian settings"))

    return out


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
:root{--bg:#fbfaf8;--fg:#1c1a17;--muted:#5f5a52;--rule:#e2ddd4;--card:#fff;
--ok:#1b5e20;--okbg:#e6f2e6;--bad:#b3261e;--badbg:#fdeceb;--na:#5f5a52;--nabg:#eeebe6;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 "Iowan Old Style",Palatino,Georgia,serif;}
main{max-width:1000px;margin:0 auto;padding:48px 24px 96px}
h1{font-size:2.1rem;line-height:1.15;margin:0 0 .3em}
h2{font-size:1.45rem;margin:2.4em 0 .5em;padding-bottom:.25em;
border-bottom:1px solid var(--rule)}
h3{font-size:1.1rem;margin:1.8em 0 .4em}
.sub{color:var(--muted);font-size:1.02rem;margin:0 0 2em}
.note{background:var(--card);border:1px solid var(--rule);border-left:3px solid #8a7f6d;
padding:14px 18px;margin:1.4em 0;font-size:.94rem}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.9rem;
font-family:ui-sans-serif,system-ui,sans-serif}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--rule);
vertical-align:top}
th{font-weight:600;background:#f3f0ea}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:2px 9px;border-radius:11px;font-size:.78rem;
font-weight:600;white-space:nowrap}
.pill.ok{color:var(--ok);background:var(--okbg)}
.pill.bad{color:var(--bad);background:var(--badbg)}
.pill.na{color:var(--na);background:var(--nabg)}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--rule);
border-radius:4px;margin:.6em 0}
.grid{stroke:#eae5dc;stroke-width:1}
.axis{stroke:#b8b0a3;stroke-width:1}
.err{stroke:#2b2b2b;stroke-width:1.3;opacity:.75}
.ytick,.xtick{font:11px ui-sans-serif,system-ui,sans-serif;fill:#5f5a52}
.ytick{text-anchor:end}
.xtick{text-anchor:middle}
.xtick.small{font-size:9px}
.barlabel{font:10px ui-sans-serif,system-ui,sans-serif;fill:#3a352e;text-anchor:middle}
.avglabel{font:11px ui-sans-serif,system-ui,sans-serif;font-weight:600}
.axislabel{font:11px ui-sans-serif,system-ui,sans-serif;fill:#5f5a52;
text-anchor:middle}
.legend{font:.85rem ui-sans-serif,system-ui,sans-serif;color:var(--muted);
margin:.2em 0 1.4em}
.key{margin-right:16px}
.key i{display:inline-block;width:11px;height:11px;border-radius:2px;
margin-right:6px;vertical-align:baseline}
.scroll{overflow-x:auto}
.missing{color:var(--muted);font-style:italic}
footer{margin-top:4em;padding-top:1.2em;border-top:1px solid var(--rule);
color:var(--muted);font-size:.85rem}
code{font:.88em ui-monospace,Menlo,Consolas,monospace;background:#f0ece5;
padding:1px 5px;border-radius:3px}
@media(prefers-color-scheme:dark){
:root{--bg:#14120f;--fg:#ece7de;--muted:#a49c8f;--rule:#332e27;--card:#1c1916;
--ok:#8fd694;--okbg:#1b2e1d;--bad:#f2938c;--badbg:#331a18;--na:#a49c8f;--nabg:#26221d}
th{background:#221f1a}
.grid{stroke:#2a2620}.axis{stroke:#4a443b}
.err{stroke:#d8d2c6}
.barlabel{fill:#c9c2b6}
code{background:#221f1a}
}
"""


def scenario_table(rows):
    order = demand_levels(rows)
    seen = {}
    for row in rows:
        key = (row["lane_mode"], row["demand_label"], row["strip_width"],
               row["mix"], row["pedestrians"])
        seen.setdefault(key, set()).add(row["seed"])
    body = []
    for key in sorted(seen, key=lambda k: (LANE_ORDER.index(k[0])
                                           if k[0] in LANE_ORDER else 9,
                                           order.index(k[1])
                                           if k[1] in order else 9)):
        lane, demand, strip, mix, peds = key
        body.append(f"<tr><td>{_esc(lane.replace('_', '-'))}</td>"
                    f"<td class='num'>{_esc(strip)}</td>"
                    f"<td>{_esc(demand)}</td><td>{_esc(mix)}</td>"
                    f"<td>{_esc(peds)}</td>"
                    f"<td class='num'>{len(seen[key])}</td></tr>")
    return ("<table><thead><tr><th>system</th><th class='num'>strip (m)</th>"
            "<th>demand</th><th>mix</th><th>pedestrians</th>"
            "<th class='num'>seeds</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table>")


def metric_table(rows, metric):
    name, unit, better = METRIC_INFO[metric]
    body = []
    for demand in demand_levels(rows):
        d = direction(rows, metric, demand)
        if d is None:
            continue
        wins = ("non-lane" if (d["non_lane"] > d["lane"]) == (better == "high")
                else "lane")
        p_t = f"{d['p_t']:.4f}" if "p_t" in d else "&mdash;"
        p_ks = f"{d['p_ks']:.4f}" if "p_ks" in d else "&mdash;"
        body.append(f"<tr><td>{_esc(demand)}</td>"
                    f"<td class='num'>{d['non_lane']:.4g}</td>"
                    f"<td class='num'>{d['lane']:.4g}</td>"
                    f"<td class='num'>{d['delta_pct']:+.1f}%</td>"
                    f"<td class='num'>{p_t}</td><td class='num'>{p_ks}</td>"
                    f"<td>{wins}</td></tr>")
    if not body:
        return "<p class='missing'>no data for this metric</p>"
    return (f"<div class='scroll'><table><thead><tr><th>demand</th>"
            f"<th class='num'>non-lane ({_esc(unit)})</th>"
            f"<th class='num'>lane ({_esc(unit)})</th>"
            f"<th class='num'>diff</th><th class='num'>p (t)</th>"
            f"<th class='num'>p (K-S)</th><th>better</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def build_html(rows, results_path, run_note):
    claims = build_claims(rows)
    supported = sum(1 for c in claims if c[2] is True)
    testable = sum(1 for c in claims if c[2] is not None)
    # Charts show the same slice the claims are judged on, or the two would
    # disagree on the page.
    prows = primary(rows)
    metrics_present = [m for m in METRIC_INFO if select(prows, m)]
    seeds = sorted({r["seed"] for r in rows})
    demands = [d for d in demand_levels(prows) if select(prows, demand=d)]
    rates = demand_rates(rows)
    rate_note = ", ".join(f"{label} = {rates[label]} veh/h"
                          for label in demand_levels(rows) if label in rates)

    claim_rows = "".join(
        f"<tr><td>{_esc(title)}</td><td><span class='pill "
        f"{'ok' if ok else 'bad' if ok is False else 'na'}'>"
        f"{'supported' if ok else 'not reproduced' if ok is False else 'no data'}"
        f"</span></td><td class='muted'>{_esc(where)}</td>"
        f"<td>{_esc(detail)}</td></tr>"
        for title, where, ok, detail in claims)

    sections = []
    for metric in metrics_present:
        name, unit, _ = METRIC_INFO[metric]
        sections.append(f"<h3>{_esc(name)}</h3>")
        sections.append(legend())
        sections.append(grouped_bars(prows, metric, name))
        sections.append(metric_table(prows, metric))
        if metric in ("link_avg_speed", "link_avg_waiting", "link_flow"):
            target = "medium" if "medium" in demands else demands[-1]
            sections.append(
                f"<p class='sub' style='margin:.8em 0 .2em'>Per link at "
                f"{_esc(target)} generation rate &mdash; the shape of the "
                f"paper's Figs. 10&ndash;12.</p>")
            sections.append(per_link_bars(prows, metric, target, name))
        if metric == "avg_speed_vehicle":
            target = "high" if "high" in demands else demands[-1]
            sections.append(
                f"<p class='sub' style='margin:.8em 0 .2em'>By vehicle type at "
                f"{_esc(target)} generation rate &mdash; the shape of the "
                f"paper's Figs. 12e&ndash;f, where it expects lane discipline "
                f"to overtake non-lane.</p>")
            sections.append(per_type_bars(prows, target, name))

    return f"""<title>RoadBird Comparison</title>
<style>{CSS}</style>
<main>
<h1>Lane vs non-lane on Dhaka's network</h1>
<p class="sub">Our DhakaSim implementation measured against
&ldquo;To Lane or Not to Lane?&rdquo; (Mushfiq et al., <em>IEEE T-ITS</em>
vol.&nbsp;25 no.&nbsp;8, pp.&nbsp;8486&ndash;8498, Aug.&nbsp;2024).
Generated {_esc(datetime.now().strftime('%Y-%m-%d %H:%M'))} from
<code>{_esc(os.path.basename(results_path))}</code>.</p>

<div class="note"><strong>Scope.</strong> {run_note}</div>

<h2>Headline</h2>
<p>Of the paper's {testable} testable claims about Dhaka, our implementation
reproduces <strong>{supported}</strong>.</p>
<div class="scroll"><table>
<thead><tr><th>claim</th><th>verdict</th><th>where</th><th>our numbers</th></tr>
</thead><tbody>{claim_rows}</tbody></table></div>

<h2>What was run</h2>
{scenario_table(rows)}
<p class="sub">Network <code>demo_backup</code> (Dhaka topology, 23 links),
1800&nbsp;s per run, hybrid car-following, GHR lane change, seeds
{_esc(', '.join(str(s) for s in seeds))}. Generation rates are per OD pair:
{_esc(rate_note)}.</p>

<h2>Results</h2>
<p class="sub">Charts and tables below hold the mix at
<em>{_esc(PRIMARY_MIX)}</em> with pedestrians <em>{_esc(PRIMARY_PEDS)}</em>
&mdash; Dhaka's own condition, and the slice the paper's Figs. 10a&ndash;c,
11a&ndash;b and 12a&ndash;b plot. The mix and pedestrian claims in the table
above vary one factor away from it.</p>
{''.join(sections)}

<h2>Reading the statistics</h2>
<p>The t-test and K-S p-values compare per-link values pooled across seeds,
which is the unit the paper's own figures plot. Links within one run share a
traffic stream and are <em>not</em> independent, so those p-values are
optimistic &mdash; read them as an indication of effect size, not as proof.
The error bars on the charts are the standard deviation across seeds, which is
the honest replication unit, and they rest on
{_esc(len(seeds))} seed{'s' if len(seeds) != 1 else ''}.</p>

<h2>What this does not test</h2>
<p>The paper's Table&nbsp;IV validates simulated travel times against
<em>observed</em> ones collected from Google Maps on real Dhaka routes
(Shankar&ndash;Palashi), reporting ME, MAE, RMSE, MAPE and RMSPE plus t-test
and K-S p-values, and concluding 9 of 12 cases match at the 5% level with a
mean MAPE accuracy of 88%. Reproducing that needs the field data, which this
project does not have. <code>experiments/stats.py</code> implements every one
of those measures, so the comparison becomes a single command once observed
travel times exist.</p>
<p>The paper's Miami and Riyadh comparisons (its Figs.&nbsp;13&nbsp;and&nbsp;14)
are also out of scope here: this sweep covers Dhaka's topology only.</p>

<footer>Generated by <code>experiments/paper_report.py</code>.
Charts are hand-built SVG; no third-party libraries, in keeping with the rest
of the project.</footer>
</main>"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("results", help="results.csv from experiments/roadbird.py")
    parser.add_argument("--out", default="experiments/paper_comparison.html")
    parser.add_argument("--note", default="", help="scope paragraph for the report")
    args = parser.parse_args(argv)

    rows = load(args.results)
    if not rows:
        raise SystemExit(f"{args.results} has no usable rows")
    note = args.note or (
        "This sweep covers Dhaka's topology only, at the heterogeneous vehicle "
        "mix with pedestrians enabled.")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(rows, args.results, note))

    print(f"{args.out}: {len(rows)} values, "
          f"{len({r['run_id'] for r in rows})} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
