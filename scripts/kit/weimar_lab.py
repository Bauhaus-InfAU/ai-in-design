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
"""Build the Weimar city kit from the prepared Weimar lab data.

    uv run scripts/kit/weimar_lab.py --source <folder with the weimar-web data>

The source folder holds weimar-buildings.geojson, weimar-plots.geojson and
weimar-streets.geojson as exported from Rhino: local metres around a model
origin, written as if they were degrees. This script places them in WGS84,
renames the fields, builds the street graph, adds the Zensus 2022 grid for the
same area and writes SOURCES.md. For any other place, use build.py.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from pyproj import Transformer
import shapely
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import ROOT, build_census, log, r6, write_json  # noqa: E402

# The Rhino export divided metres by this factor to make them look like degrees.
DEGREE = 111319.49
# Model origin in ETRS89 / UTM zone 32N (EPSG:25832). Found by matching the
# building centroids to the Thuringian LoD2 tiles 32_662_5648 and 32_662_5650.
ORIGIN = (662212.27, 5648063.18)

USES = {
    "Generic Residential": "residential",
    "Generic Office Building": "office",
    "Generic Retail": "retail",
    "Generic Food and Beverage Service": "food_and_drink",
    "Generic Service": "service",
    "Generic Education": "education",
    "Generic Culture": "culture",
    "Generic Entertainment": "entertainment",
    "Generic Civic Function": "civic",
    "Generic Health and Wellbeing": "health",
    "Generic Accommodation": "accommodation",
    "Generic Sport Facility": "sport",
    "Generic Transportation Service": "transport",
    "Generic Light Industrial": "light_industrial",
    "Undefined Land use": "undefined",
}

PLOT_USES = {
    "Residential": "residential",
    "Mixeduse": "mixed use",
    "Special functions": "special functions",
    "Industrial and commercial": "industrial and commercial",
    "Recreation": "recreation",
    "Square": "square",
    "Flowing water": "flowing water",
    "Standing water": "standing water",
    "Wasteland": "wasteland",
    "Cemetery": "cemetery",
}

MIN_AREA_M2 = 1  # smaller plots are slivers left over from drawing

to_wgs = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True).transform


def place(coords):
    """Model coordinates (pseudo-degrees) to [lon, lat]."""
    if isinstance(coords[0], (int, float)):
        lon, lat = to_wgs(coords[0] * DEGREE + ORIGIN[0], coords[1] * DEGREE + ORIGIN[1])
        return [r6(lon), r6(lat)]
    return [place(c) for c in coords]


def outline(geometry):
    """The Rhino export gives each footprint as a pile of mesh triangles. Merge
    them into one outline in metres, snapped to 1 cm, and return it as [lon, lat]
    GeoJSON, or None if nothing is left."""
    g = shapely.transform(shape(geometry), lambda xy: xy * DEGREE + ORIGIN)
    g = shapely.simplify(shapely.unary_union(shapely.set_precision(g, 0.01)).buffer(0), 0.02)
    if g.is_empty or g.area < MIN_AREA_M2:
        return None
    g = shapely.transform(g, lambda x, y: to_wgs(x, y), interleaved=False)
    return round_coords(mapping(g))


def round_coords(geojson):
    def walk(c):
        return [r6(c[0]), r6(c[1])] if isinstance(c[0], (int, float)) else [walk(x) for x in c]
    return {"type": geojson["type"], "coordinates": walk(geojson["coordinates"])}


def read(source, name):
    return json.loads((source / name).read_text(encoding="utf-8"))["features"]


def build_buildings(source, out):
    features = []
    seen = {}
    uses_total = {}
    for f in read(source, "weimar-buildings.geojson"):
        p = f["properties"]
        bid = p["Building ID"]
        seen[bid] = seen.get(bid, 0) + 1
        if seen[bid] > 1:
            bid = f"{bid}-{seen[bid]}"
        areas = {USES[k]: round(float(v), 1) for k, v in p.items() if k in USES and float(v) > 0}
        main = max(areas, key=areas.get) if areas else None
        geometry = outline(f["geometry"])
        if geometry is None:
            continue
        for k, v in areas.items():
            uses_total[k] = uses_total.get(k, 0) + v
        features.append({
            "type": "Feature",
            "id": bid,
            "properties": {
                "id": bid,
                "height": round(float(p["Height"]), 1),
                "height_source": "lod2",
                "floors": int(float(p["Floors"])),
                "use": main,
                "floor_area_m2": areas,
            },
            "geometry": geometry,
        })
    write_json(out / "buildings.geojson", {"type": "FeatureCollection", "features": features})
    return {"count": len(features), "uses": {k: round(v) for k, v in sorted(uses_total.items(), key=lambda kv: -kv[1])}}


def build_plots(source, out):
    features = []
    counts = {}
    for f in read(source, "weimar-plots.geojson"):
        geometry = outline(f["geometry"])
        if geometry is None:
            continue
        i = len(features)
        use = PLOT_USES.get(f["properties"]["landuse"], f["properties"]["landuse"])
        counts[use] = counts.get(use, 0) + 1
        features.append({
            "type": "Feature",
            "id": i,
            "properties": {"id": i, "landuse": use},
            "geometry": geometry,
        })
    write_json(out / "plots.geojson", {"type": "FeatureCollection", "features": features})
    return {"count": len(features), "landuse": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


def build_streets(source, out):
    nodes = {}
    edges = []
    lines = []

    def node(c):
        key = (round(c[0], 9), round(c[1], 9))
        if key not in nodes:
            lon, lat = place(c[:2])
            nodes[key] = {"id": len(nodes), "lon": lon, "lat": lat}
        return nodes[key]

    for i, f in enumerate(read(source, "weimar-streets.geojson")):
        coords = f["geometry"]["coordinates"]
        a, b = node(coords[0]), node(coords[-1])
        line = [[a["lon"], a["lat"]]] + [place(c[:2]) for c in coords[1:-1]] + [[b["lon"], b["lat"]]]
        length = round(float(f["properties"]["length"]), 1)
        edges.append({"id": i, "from": a["id"], "to": b["id"], "length": length, "walkable": True, "coords": line})
        lines.append({
            "type": "Feature",
            "id": i,
            "properties": {"length": length, "walkable": True},
            "geometry": {"type": "LineString", "coordinates": line},
        })
    write_json(out / "streets.graph.json", {"nodes": list(nodes.values()), "edges": edges})
    write_json(out / "streets.geojson", {"type": "FeatureCollection", "features": lines})
    return {"nodes": len(nodes), "edges": len(edges), "length_km": round(sum(e["length"] for e in edges) / 1000, 1)}


def extent(out):
    xs, ys = [], []
    for name in ("buildings.geojson", "plots.geojson", "streets.geojson"):
        for f in json.loads((out / name).read_text(encoding="utf-8"))["features"]:
            b = shape(f["geometry"]).bounds
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    return min(xs), min(ys), max(xs), max(ys)


def write_sources(out, bbox, size, today, stats):
    b, p, s, z = stats["buildings"], stats["plots"], stats["streets"], stats["census"]
    west, south, east, north = bbox
    uses = ", ".join(f"`{k}`" for k in b["uses"])
    plot_uses = "\n".join(f"| `{k}` | {n} |" for k, n in p["landuse"].items())
    text = f"""# Sources

The city kit: central Weimar, about {size[0]} × {size[1]} m, west of and including the Markt. Generated on {today} by `scripts/kit/weimar_lab.py` from data prepared for an earlier course. Don't edit these files by hand; run the script again.

Bounding box, west, south, east, north: {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}. All coordinates are WGS84 longitude and latitude, longitude first.

| File | Source | Licence |
| --- | --- | --- |
| `buildings.geojson` | footprints and heights: 3D building models LoD2 of Thuringia; uses: Egor Gaydukov | dl-de/by-2-0 |
| `plots.geojson` | land use per plot: Egor Gaydukov | |
| `streets.geojson`, `streets.graph.json` | pedestrian network: Egor Gaydukov | |
| `zensus.geojson` | Zensus 2022, 100 m grid | dl-de/by-2-0 |
| `kit.json` | the generator | |

## Attribution

Put this line on every map or page that uses the kit:

> Buildings: 3D building models LoD2 © GDI-Th (dl-de/by-2-0). Land use and pedestrian network: Egor Gaydukov, InfAU, Bauhaus-Universität Weimar. Census grid: Zensus 2022, © Statistische Ämter des Bundes und der Länder (dl-de/by-2-0).

## `buildings.geojson`

Footprints and heights from the 3D building models in level of detail 2 (LoD2) of the Thüringer Landesamt für Bodenmanagement und Geoinformation, tiles 32_662_5648 and 32_662_5650, surveyed 14 October 2024. Licence: Datenlizenz Deutschland, Namensnennung, Version 2.0 (dl-de/by-2-0), © GDI-Th. Floors and floor area by use were added by Egor Gaydukov.

{b['count']} buildings. Every building has a height.

| Field | Meaning | Unit |
| --- | --- | --- |
| `id` | building id | |
| `height` | height of the building, from the ground to the highest point of the roof | metres |
| `height_source` | always `lod2`: measured in the 3D building model | |
| `floors` | floors above ground | floors |
| `use` | the use with the largest floor area in the building | |
| `floor_area_m2` | floor area per use, only uses above zero | m² |

The uses are {uses}. `undefined` is floor area whose use is not known. Floor areas are estimates for the whole building, not surveyed per floor.

## `plots.geojson`

Plots with their land use, prepared by Egor Gaydukov. {p['count']} plots.

| `landuse` | Plots |
| --- | --- |
{plot_uses}

## `streets.geojson` and `streets.graph.json`

A pedestrian network drawn by Egor Gaydukov for a walking simulation. It follows streets, paths and squares, and crosses squares where people walk across them. Every edge is walkable.

`streets.geojson` is the drawing: one straight line per edge, with `length` and `walkable`.

`streets.graph.json` is the network, for routing:

- `nodes`: `id`, `lon`, `lat`. A node is a junction or a dead end.
- `edges`: `id`, `from` and `to` (node ids), `length` in metres, `walkable`, and `coords`, the line from `from` to `to`.

Edges have no direction; you can walk them both ways. {s['nodes']} nodes, {s['edges']} edges, {s['length_km']} km in total, all in one connected network.

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

## `kit.json`

The kit area: `label`, `center` as `[lon, lat]`, `bbox` as `[west, south, east, north]`, `size_m` as `[width, height]`, and `generated`.
"""
    (out / "SOURCES.md").write_text(text, encoding="utf-8")
    log(f"  wrote {(out / 'SOURCES.md').relative_to(ROOT)}")


def main():
    p = argparse.ArgumentParser(description="Build the Weimar city kit from the prepared lab data.")
    p.add_argument("--source", required=True, help="folder with weimar-buildings/plots/streets.geojson")
    p.add_argument("--out", default=str(ROOT / "public" / "data"), help="output folder")
    args = p.parse_args()
    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()

    log("Buildings, plots and streets from the lab data ...")
    stats = {
        "buildings": build_buildings(source, out),
        "plots": build_plots(source, out),
        "streets": build_streets(source, out),
    }
    bbox = extent(out)
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform
    (x0, y0), (x1, y1) = to_m(bbox[0], bbox[1]), to_m(bbox[2], bbox[3])
    size = [round(x1 - x0), round(y1 - y0)]
    stats["census"] = build_census(bbox, out)

    write_json(out / "kit.json", {
        "label": "Weimar",
        "center": [r6((bbox[0] + bbox[2]) / 2), r6((bbox[1] + bbox[3]) / 2)],
        "bbox": [r6(v) for v in bbox],
        "size_m": size,
        "generated": today,
    })
    write_sources(out, bbox, size, today, stats)
    log("Done.")
    log(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
