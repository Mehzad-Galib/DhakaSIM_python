#!/usr/bin/env python3
"""Write a GeoJSON street grid for a planned district.

A stand-in for an OpenStreetMap extract where one is not to hand.  The output
is ordinary GeoJSON with the tags :mod:`make_network` reads, so it goes through
exactly the same conversion as real data -- projection, splitting at junctions,
dual-carriageway fusion, width inference -- rather than writing ``node.txt``
directly and bypassing all of it.

The grids here are idealised, not surveyed.  They carry the carriageway widths
the RoadBird paper states for Miami and Riyadh, which is the property its
conclusion turns on: Riyadh's roads are wide and long enough for lane
discipline to pay off, and Dhaka's are not.  They are *not* those cities'
actual topology, and the networks they produce say so in their geometry.txt.

    python make_grid.py miami --out miami.geojson
    python make_network.py miami.geojson --out input/miami

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math

# Each district: centre, how far apart the streets run, and the widths.
# Arterials are divided, so they are written as two one-way carriageways the
# converter has to recognise and fuse back together -- as it would for the real
# thing.
DISTRICTS = {
    "miami": {
        "description": "Homestead / Florida City grid, south Miami-Dade",
        "centre": (25.4775, -80.4725),
        "spacing": 500.0,          # metres between parallel streets
        "count": 3,                # streets each way, so a 3x3 grid
        "stub": 200.0,             # how far each street runs past the grid
        "local_width": 10.0,       # the paper's narrower Miami carriageway
        "arterial_carriageway": 8.0,
        "arterial_gap": 12.0,      # gives a 20 m road with a 4 m median
        "local_name": "SW Street",
        "arterial_name": "Palm Drive",
    },
    "riyadh": {
        "description": "Al Malaz grid, central Riyadh",
        # Riyadh's blocks are longer and its roads wider than Miami's, which is
        # the whole reason the paper finds lane discipline pays off there
        "centre": (24.6800, 46.7400),
        "spacing": 750.0,
        "count": 3,
        "stub": 300.0,
        "local_width": 18.0,       # the paper's narrower Riyadh carriageway
        "arterial_carriageway": 10.0,
        "arterial_gap": 14.0,      # gives a 24 m road with a 4 m median
        "local_name": "Al Malaz Street",
        "arterial_name": "King Abdulaziz Road",
    },
}


def metres_to_lonlat(lat0: float, lon0: float):
    """Inverse of make_network.Projector, so a round trip lands where it began."""
    phi = math.radians(lat0)
    m_per_deg_lat = (111132.92 - 559.82 * math.cos(2 * phi)
                     + 1.175 * math.cos(4 * phi) - 0.0023 * math.cos(6 * phi))
    m_per_deg_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
                     + 0.118 * math.cos(5 * phi))

    def convert(x: float, y: float):
        # y grows southwards in the projected frame, so it subtracts here
        return [lon0 + x / m_per_deg_lon, lat0 - y / m_per_deg_lat]
    return convert


def feature(coords, props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords}}


def build(spec) -> dict:
    lat0, lon0 = spec["centre"]
    to_lonlat = metres_to_lonlat(lat0, lon0)
    count, spacing = spec["count"], spec["spacing"]

    extent = (count - 1) * spacing
    offsets = [i * spacing - extent / 2.0 for i in range(count)]
    middle = count // 2                      # the arterial in each direction
    half_gap = spec["arterial_gap"] / 2.0
    # Streets run past the outermost crossing so their far ends are dead ends
    # rather than junctions.  Those degree-1 nodes are where vehicles enter and
    # leave: without them run_sim.py has no origin/destination pairs at all.
    reach = extent / 2.0 + spec["stub"]

    # Where the cross-streets sit along a road.  OSM puts a shared node at
    # every intersection, and the converter splits ways only where they
    # actually share one -- two lines that merely cross on paper are a bridge,
    # not a junction.  So the crossings have to be real vertices here too.  A
    # divided arterial is crossed twice, once per carriageway.
    crossings = []
    for index, offset in enumerate(offsets):
        if index == middle:
            crossings += [offset - half_gap, offset + half_gap]
        else:
            crossings.append(offset)
    along = sorted({-reach, *crossings, reach})

    features = []
    for axis in ("ew", "ns"):
        for index, offset in enumerate(offsets):
            arterial = index == middle
            name = spec["arterial_name"] if arterial else \
                f"{spec['local_name']} {index + 1}"

            def line(shift=0.0, reverse=False):
                if axis == "ew":
                    points = [(v, offset + shift) for v in along]
                else:
                    points = [(offset + shift, v) for v in along]
                if reverse:
                    points.reverse()
                return [to_lonlat(x, y) for x, y in points]

            if arterial:
                # two one-way carriageways either side of the centreline,
                # running opposite ways, as OSM maps a divided road
                width = spec["arterial_carriageway"]
                features.append(line_feature(line(-half_gap), name, width, True))
                features.append(line_feature(line(half_gap, reverse=True),
                                             name, width, True))
            else:
                features.append(line_feature(line(), name,
                                             spec["local_width"], False))
    return {"type": "FeatureCollection",
            "generator": "make_grid.py (idealised, not surveyed)",
            "features": features}


def line_feature(coords, name, width, oneway):
    props = {"highway": "primary" if oneway else "secondary",
             "name": name, "width": str(width)}
    if oneway:
        props["oneway"] = "yes"
    return feature(coords, props)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("district", choices=sorted(DISTRICTS))
    parser.add_argument("--out", required=True, help="GeoJSON file to write")
    args = parser.parse_args(argv)

    spec = DISTRICTS[args.district]
    data = build(spec)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)

    extent = (spec["count"] - 1) * spec["spacing"]
    print(f"{args.out}: {len(data['features'])} ways, "
          f"{spec['count']}x{spec['count']} grid over {extent:.0f} m "
          f"plus {spec['stub']:.0f} m stubs, "
          f"centre {spec['centre'][0]:.4f},{spec['centre'][1]:.4f}")
    print(f"  {spec['description']} -- idealised, not surveyed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
