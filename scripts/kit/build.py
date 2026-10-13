# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "overturemaps",
#     "geopandas",
#     "pyarrow",
#     "pandas",
#     "pyproj",
#     "shapely",
#     "requests",
# ]
# ///
"""Build the city kit in public/data/ from open data.

    uv run scripts/kit/build.py --place "Weimar" --size 2000
    uv run scripts/kit/build.py --center 50.9795,11.3290 --size 1500

Writes buildings.geojson, streets.geojson, streets.graph.json, zensus.geojson,
kit.json and SOURCES.md. Buildings and streets come from Overture Maps, which
builds on OpenStreetMap. The census grid comes from the Zensus 2022 100 m grid
and only covers Germany. Census downloads are cached in scripts/kit/.cache/.
"""

import argparse
import datetime as dt
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests
from overturemaps import core as overture
from pyproj import Transformer
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping
from shapely.ops import substring

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = HERE / ".cache"
USER_AGENT = "ai-in-design city kit generator"

FLOOR_HEIGHT = 3.0  # metres per floor, when Overture gives floors but no height

NOT_WALKABLE_CLASS = {"motorway", "trunk"}

ZENSUS_BASE = "https://www.destatis.de/static/DE/zensus/gitterdaten/"
ZENSUS_FILES = {
    "population": ("Zensus2022_Bevoelkerungszahl.zip", "Einwohner"),
    "avg_age": ("Durchschnittsalter_in_Gitterzellen.zip", "Durchschnittsalter"),
    "rent_eur_m2": ("Zensus2022_Durchschn_Nettokaltmiete.zip", "durchschnMieteQM"),
    "vacancy_pct": ("Leerstandsquote_in_Gitterzellen.zip", "Leerstandsquote"),
    "heating": ("Gebaeude_mit_Wohnraum_nach_ueberwiegender_Heizungsart.zip", None),
}
HEATING_TYPES = {
    "Fernheizung": "district heating",
    "Etagenheizung": "floor heating (one system per flat)",
    "Blockheizung": "block heating",
    "Zentralheizung": "central heating",
    "Einzel_Mehrraumoefen": "stoves",
    "keine_Heizung": "no heating",
}


def log(msg):
    print(msg, flush=True)


def r6(x):
    return round(float(x), 6)


def missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- area


def geocode(place):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    hits = r.json()
    if not hits:
        sys.exit(f"Nominatim found no place called {place!r}. Try --center lat,lon instead.")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


def kit_area(args):
    if args.center:
        lat, lon = (float(v) for v in args.center.split(","))
        label = args.place or f"{lat:.5f}, {lon:.5f}"
    else:
        lat, lon = geocode(args.place)
        label = args.place
    local = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +units=m +ellps=WGS84"
    to_local = Transformer.from_crs("EPSG:4326", local, always_xy=True).transform
    to_wgs = Transformer.from_crs(local, "EPSG:4326", always_xy=True).transform
    half = args.size / 2
    corners = [to_wgs(x, y) for x in (-half, half) for y in (-half, half)]
    bbox = (
        min(c[0] for c in corners), min(c[1] for c in corners),
        max(c[0] for c in corners), max(c[1] for c in corners),
    )
    return label, (lat, lon), bbox, to_local, to_wgs


def round_coords(geom):
    def ring(coords):
        return [[r6(x), r6(y)] for x, y, *_ in coords]

    if isinstance(geom, Polygon):
        return {"type": "Polygon", "coordinates": [ring(geom.exterior.coords)] + [ring(i.coords) for i in geom.interiors]}
    if isinstance(geom, MultiPolygon):
        return {
            "type": "MultiPolygon",
            "coordinates": [[ring(p.exterior.coords)] + [ring(i.coords) for i in p.interiors] for p in geom.geoms],
        }
    return mapping(geom)


def fetch(kind, bbox, release):
    log(f"  downloading Overture {kind}s ...")
    return overture.geodataframe(kind, bbox=bbox, release=release, stac=True)


def primary_name(names):
    if isinstance(names, dict) and names.get("primary"):
        return names["primary"]
    return None


# ---------------------------------------------------------------- buildings


def height_source(sources):
    """The dataset Overture took the height from: a source for the height
    property itself, else the source of the whole feature."""
    whole = None
    for s in sources:
        if s.get("property") == "/properties/height":
            return s.get("dataset")
        if s.get("property") in ("", None) and whole is None:
            whole = s.get("dataset")
    return whole


def build_buildings(bbox, release, out):
    log("Buildings from Overture Maps ...")
    gdf = fetch("building", bbox, release)
    area = box(*bbox)
    features = []
    counts = {}
    datasets = set()
    for _, row in gdf.iterrows():
        geom = row["geometry"]
        if geom is None or geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if row["is_underground"] is True or not area.contains(geom.representative_point()):
            continue
        floors = None if missing(row["num_floors"]) else int(row["num_floors"])
        height = None if missing(row["height"]) else round(float(row["height"]), 1)
        sources = row["sources"] if row["sources"] is not None else []
        dataset = height_source(sources)
        for s in sources:
            datasets.add(s.get("dataset"))
        if height is not None and dataset == "OpenStreetMap":
            source = "osm"
        elif floors is not None:
            height = round(floors * FLOOR_HEIGHT, 1)
            source = "floors"
        elif height is not None:
            source = "ml"
        else:
            source = None
        counts[source] = counts.get(source, 0) + 1
        features.append({
            "type": "Feature",
            "id": row["id"],
            "properties": {
                "id": row["id"],
                "class": None if missing(row["class"]) else row["class"],
                "name": primary_name(row["names"]),
                "floors": floors,
                "height": height,
                "height_source": source,
            },
            "geometry": round_coords(geom),
        })
    write_json(out / "buildings.geojson", {"type": "FeatureCollection", "features": features})
    return {
        "count": len(features),
        "osm": counts.get("osm", 0),
        "floors": counts.get("floors", 0),
        "ml": counts.get("ml", 0),
        "missing": counts.get(None, 0),
        "datasets": sorted(d for d in datasets if d),
    }


# ---------------------------------------------------------------- streets


def is_walkable(row):
    if row["class"] in NOT_WALKABLE_CLASS:
        return False
    rules = row["access_restrictions"]
    if rules is None or (isinstance(rules, float) and math.isnan(rules)):
        return True
    foot_ok = False
    foot_denied = False
    all_denied = False
    for rule in rules:
        when = rule.get("when") or {}
        modes = list(when.get("mode") if when.get("mode") is not None else [])
        unconditional = not any(when.get(k) is not None for k in ("during", "heading", "using", "recognized", "vehicle"))
        if rule.get("access_type") in ("allowed", "designated") and "foot" in modes:
            foot_ok = True
        if rule.get("access_type") == "denied" and unconditional and rule.get("between") is None:
            if "foot" in modes:
                foot_denied = True
            if not modes:
                all_denied = True
    if foot_denied:
        return False
    return foot_ok or not all_denied


def build_streets(bbox, release, to_local, to_wgs, out):
    log("Streets from Overture Maps ...")
    segments = fetch("segment", bbox, release)
    segments = segments[segments["subtype"] == "road"]
    connectors = fetch("connector", bbox, release)
    point_of = {c.id: (c.geometry.x, c.geometry.y) for c in connectors.itertuples(index=False)}
    area = box(*bbox)

    nodes = {}
    edges = []
    lines = []
    for _, seg in segments.iterrows():
        cons = sorted(
            [(float(c["at"]), c["connector_id"]) for c in (seg["connectors"] if seg["connectors"] is not None else [])],
            key=lambda c: c[0],
        )
        if len(cons) < 2:
            continue
        local = shapely.transform(seg.geometry, lambda x, y: to_local(x, y), interleaved=False)
        walkable = is_walkable(seg)
        name = primary_name(seg["names"])
        cls = None if missing(seg["class"]) else seg["class"]
        for (a, ca), (b, cb) in zip(cons, cons[1:]):
            if b <= a:
                continue
            piece = substring(local, a, b, normalized=True)
            if not isinstance(piece, LineString) or piece.length == 0:
                continue
            wgs = shapely.transform(piece, lambda x, y: to_wgs(x, y), interleaved=False)
            if not wgs.intersects(area):
                continue
            coords = [[r6(x), r6(y)] for x, y in wgs.coords]
            for cid, xy in ((ca, coords[0]), (cb, coords[-1])):
                if cid not in nodes:
                    lon, lat = point_of.get(cid, xy)
                    nodes[cid] = {"id": cid, "lon": r6(lon), "lat": r6(lat)}
            i = len(edges)
            edges.append({
                "id": i,
                "from": ca,
                "to": cb,
                "length": round(piece.length, 1),
                "walkable": walkable,
                "class": cls,
                "name": name,
                "coords": coords,
            })
            lines.append({
                "type": "Feature",
                "id": i,
                "properties": {"class": cls, "name": name, "walkable": walkable},
                "geometry": {"type": "LineString", "coordinates": coords},
            })
    write_json(out / "streets.graph.json", {"nodes": list(nodes.values()), "edges": edges})
    write_json(out / "streets.geojson", {"type": "FeatureCollection", "features": lines})
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "walkable_edges": sum(e["walkable"] for e in edges),
        "length_km": round(sum(e["length"] for e in edges) / 1000, 1),
    }


# ---------------------------------------------------------------- census


def zensus_csv(zip_name):
    path = CACHE / "zensus" / zip_name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log(f"  downloading {zip_name} ...")
        r = requests.get(ZENSUS_BASE + zip_name, timeout=600)
        r.raise_for_status()
        path.write_bytes(r.content)
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith("_100m-Gitter.csv"))
        return io.BytesIO(z.read(name))


def number(value):
    """Zensus values: '12,5' is a number, '–' means exactly zero (or set to zero)."""
    value = str(value).strip()
    if value in ("–", "-"):
        return 0.0
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def build_census(bbox, out):
    log("Census grid from Zensus 2022 ...")
    to3035 = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    to4326 = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
    west, south, east, north = bbox
    xs, ys = zip(*(to3035.transform(x, y) for x in (west, east) for y in (south, north)))
    # Every 100 m cell whose square touches the kit, kept whole.
    xmin, xmax, ymin, ymax = min(xs) - 50, max(xs) + 50, min(ys) - 50, max(ys) + 50

    cells = {}
    uncertain = {}
    for field, (zip_name, column) in ZENSUS_FILES.items():
        df = pd.read_csv(zensus_csv(zip_name), sep=";", dtype=str, encoding="utf-8")
        df["x"] = df["x_mp_100m"].astype(int)
        df["y"] = df["y_mp_100m"].astype(int)
        df = df[(df.x > xmin) & (df.x < xmax) & (df.y > ymin) & (df.y < ymax)]
        for _, row in df.iterrows():
            cell = cells.setdefault(row["GITTER_ID_100m"], {"x": row["x"], "y": row["y"]})
            if field == "heating":
                counts = {k: number(row[k]) or 0 for k in HEATING_TYPES}
                top = max(counts.values())
                winners = [HEATING_TYPES[k] for k, n in counts.items() if n == top and n > 0]
                cell["heating_main"] = " / ".join(winners) if winners else None
                cell["heating_buildings"] = int(number(row["Insgesamt_Heizungsart"]) or 0)
            else:
                value = number(row[column])
                cell[field] = int(value) if field == "population" and value is not None else value
                if str(row.get("werterlaeuternde_Zeichen", "")).strip() == "KLAMMERN":
                    uncertain.setdefault(row["GITTER_ID_100m"], []).append(field)

    if not cells:
        log("  no census cells here; the Zensus grid only covers Germany. Skipping zensus.geojson.")
        return None

    fields = ["population", "avg_age", "rent_eur_m2", "vacancy_pct", "heating_main", "heating_buildings"]
    features = []
    for cid, c in sorted(cells.items()):
        x, y = c["x"], c["y"]
        ring = [to4326.transform(px, py) for px, py in
                ((x - 50, y - 50), (x + 50, y - 50), (x + 50, y + 50), (x - 50, y + 50), (x - 50, y - 50))]
        props = {"id": cid}
        props.update({f: c.get(f) for f in fields})
        props["uncertain"] = uncertain.get(cid, [])
        features.append({
            "type": "Feature",
            "id": cid,
            "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[[r6(a), r6(b)] for a, b in ring]]},
        })
    write_json(out / "zensus.geojson", {"type": "FeatureCollection", "features": features})
    return {
        "cells": len(features),
        "cells_with_population": sum(1 for f in features if f["properties"]["population"] is not None),
        "population_sum": sum(f["properties"]["population"] or 0 for f in features),
        "cells_with_rent": sum(1 for f in features if f["properties"]["rent_eur_m2"] is not None),
    }


# ---------------------------------------------------------------- sources


def write_sources(out, label, center, bbox, size, today, release, stats):
    b, s, z = stats["buildings"], stats["streets"], stats["census"]
    west, south, east, north = bbox
    holders = ["© OpenStreetMap contributors"]
    if "Microsoft ML Buildings" in b["datasets"]:
        holders.append("Microsoft")
    attribution = f"Buildings and streets: Overture Maps Foundation, {', '.join(holders)} (ODbL)."
    census_row = ""
    census_block = ""
    if z:
        census_row = "| `zensus.geojson` | Zensus 2022, 100 m grid | dl-de/by-2-0 |\n"
        attribution += " Census grid: Zensus 2022, © Statistische Ämter des Bundes und der Länder (dl-de/by-2-0)."
        census_block = f"""
## `zensus.geojson`

The Zensus 2022 census on a 100 m grid, reference date 15 May 2022. Source: Statistische Ämter des Bundes und der Länder, https://www.zensus2022.de, files from https://www.zensus2022.de/DE/Ergebnisse-des-Zensus/gitterzellen.html. Licence: Datenlizenz Deutschland, Namensnennung, Version 2.0 (dl-de/by-2-0, https://www.govdata.de/dl-de/by-2-0).

{z['cells']} cells, kept whole wherever a cell touches the kit area. Each cell is a 100 m square in the European grid (EPSG:3035), stored here as a polygon in longitude and latitude.

| Field | Meaning | Unit |
| --- | --- | --- |
| `id` | the census grid id of the cell | |
| `population` | residents | persons |
| `avg_age` | average age of the residents | years |
| `rent_eur_m2` | average net rent without heating and service charges (Nettokaltmiete) | € per m² per month |
| `vacancy_pct` | share of flats standing empty | percent |
| `heating_main` | the most common main heating type of the residential buildings in the cell; ties are joined with " / " | |
| `heating_buildings` | residential buildings counted for `heating_main` | buildings |
| `uncertain` | fields the census marks as statistically unreliable, because they rest on very few cases | |

How to read it:

- A cell is an area, not a building. To share a cell's residents out, use the part of the cell's area that you need.
- `null` means the census published no figure for that field in that cell. A cell with `population: null` is in the kit because another table lists it, for example because it has flats or residential buildings, but the census published no resident count for it. Don't read `null` as zero. Leave such cells out of sums and say how many you left out.
- The census protects privacy by changing small counts slightly at random (the cell-key method). Single cells are therefore approximate, and cell values don't add up exactly to official totals.
- In the source tables, "–" means exactly zero or set to zero. The kit stores it as `0`.

{z['cells_with_population']} of the {z['cells']} cells have a resident count, {z['population_sum']} residents in total. {z['cells_with_rent']} cells have a rent figure.
"""

    ml_line = ""
    if b["ml"]:
        ml_line = f"\n- `ml`: a height that Microsoft estimated from aerial images with machine learning, not measured. {b['ml']} buildings."

    text = f"""# Sources

The city kit: {label}, a square of {size} × {size} m around {center[0]:.5f} N, {center[1]:.5f} E. Generated on {today} by `scripts/kit/build.py`. Don't edit these files by hand; run the generator again.

Bounding box, west, south, east, north: {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}. All coordinates are WGS84 longitude and latitude, longitude first.

| File | Source | Licence |
| --- | --- | --- |
| `buildings.geojson` | Overture Maps, release {release} | ODbL |
| `streets.geojson`, `streets.graph.json` | Overture Maps, release {release} | ODbL |
{census_row}| `kit.json` | the generator | |

## Attribution

Put this line on every map or page that uses the kit:

> {attribution}

## `buildings.geojson`

Building footprints from Overture Maps (https://overturemaps.org), release {release}, theme `buildings`. Overture combines OpenStreetMap with other sources; the sources in this area are: {', '.join(b['datasets'])}. Licence: Open Database License (ODbL), https://opendatacommons.org/licenses/odbl/. A building is in the kit when a point inside its footprint lies in the kit area. Underground buildings are left out.

{b['count']} buildings.

| Field | Meaning | Unit |
| --- | --- | --- |
| `id` | Overture id | |
| `class` | building type, such as `apartments`, `house` or `church`; `null` when unknown | |
| `name` | name, where there is one | |
| `floors` | floors above ground | floors |
| `height` | height of the building | metres |
| `height_source` | where `height` comes from | |

`height_source` is one of:

- `osm`: the height mapped in OpenStreetMap. {b['osm']} buildings.
- `floors`: the source gives the number of floors but no mapped height. The height is floors × {FLOOR_HEIGHT:g} m, an estimate. {b['floors']} buildings.{ml_line}
- `null`: no height and no floors. `height` is `null` too. {b['missing']} buildings. Don't guess these; use a stated default and mark them.

## `streets.geojson` and `streets.graph.json`

Streets and paths from Overture Maps, release {release}, theme `transportation`, built from OpenStreetMap (ODbL, as above). Every road and path: roads, footways, paths, steps. Railways are left out.

`streets.geojson` is the drawing: one line per street segment, with `class`, `name` and `walkable`.

`streets.graph.json` is the network, for routing:

- `nodes`: `id`, `lon`, `lat`. A node is a junction or a dead end.
- `edges`: `id`, `from` and `to` (node ids), `length` in metres along the street, `walkable`, `class`, `name`, and `coords`, the line from `from` to `to`.

Edges have no direction; you can walk them both ways. `walkable` is false for motorways and trunk roads and for ways closed to people on foot. Edges that cross the edge of the kit are kept whole, so some lines end a little outside it. {s['walkable_edges']} of {s['edges']} edges are walkable, {s['nodes']} nodes, {s['length_km']} km of streets and paths in total.
{census_block}
## `kit.json`

The kit area: `label`, `center` as `[lon, lat]`, `bbox` as `[west, south, east, north]`, `size_m` as `[width, height]`, `overture_release` and `generated`.
"""
    (out / "SOURCES.md").write_text(text, encoding="utf-8")
    log(f"  wrote {(out / 'SOURCES.md').relative_to(ROOT)}")


# ---------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description="Build the city kit in public/data/.")
    p.add_argument("--place", help='a place name, such as "Weimar"')
    p.add_argument("--center", help="lat,lon of the centre, instead of looking up --place")
    p.add_argument("--size", type=float, default=2000, help="side of the square in metres (default 2000)")
    p.add_argument("--out", default=str(ROOT / "public" / "data"), help="output folder")
    p.add_argument("--release", help="Overture release, such as 2026-09-23.1 (default: the latest)")
    p.add_argument("--no-census", action="store_true", help="skip the Zensus grid")
    args = p.parse_args()
    if not args.place and not args.center:
        p.error("give --place or --center")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    release = args.release or overture.get_latest_release()

    label, center, bbox, to_local, to_wgs = kit_area(args)
    log(f"Kit: {label}, {args.size:g} m square around {center[0]:.5f}, {center[1]:.5f}, Overture {release}")
    today = dt.date.today().isoformat()

    stats = {
        "buildings": build_buildings(bbox, release, out),
        "streets": build_streets(bbox, release, to_local, to_wgs, out),
        "census": None if args.no_census else build_census(bbox, out),
    }
    if stats["census"] is None:
        (out / "zensus.geojson").unlink(missing_ok=True)

    write_json(out / "kit.json", {
        "label": label,
        "center": [r6(center[1]), r6(center[0])],
        "bbox": [r6(v) for v in bbox],
        "size_m": [args.size, args.size],
        "overture_release": release,
        "generated": today,
    })
    write_sources(out, label, center, bbox, int(args.size), today, release, stats)
    log("Done.")
    log(json.dumps(stats, indent=2))


if __name__ == "__main__":
    sys.exit(main())
