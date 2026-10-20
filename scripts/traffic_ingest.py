#!/usr/bin/env python3
"""
traffic_ingest.py

Pulls Hackney's proposed traffic orders straight from Traffweb's WFS and
writes them as CSV in the same shape as the planning rows.

No PDF, no transcription. This is the traffic half of the notices portal.

What it does:
  * queries pordersl (lines), pordersp (points) and pordersr (regions)
  * parses GML with the standard library
  * converts EPSG:27700 eastings/northings to WGS84 lat/lon in pure Python
  * groups locations into schemes by order_ref
  * computes open/closed from date_close against today, at run time, so a
    closed consultation can never render as open
  * writes traffic-locations-YYYY-MM-DD.csv and traffic-schemes-YYYY-MM-DD.csv

Standard library only.

Usage:
    python3 traffic_ingest.py
    python3 traffic_ingest.py --council hackney
    python3 traffic_ingest.py --myorders "1,2,3,1022,1025"   # test a filter
"""

import argparse
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://mapserver.traffweb.app/cgi-bin/{council}/consult"
LAYERS = ["pordersl", "pordersp", "pordersr"]
NS = {
    "wfs": "http://www.opengis.net/wfs",
    "gml": "http://www.opengis.net/gml",
    "ms": "http://mapserver.gis.umn.edu/mapserver",
}
UA = "notices-ingest/1.0 (Hackney Citizen; editorial)"


# ------------------------------------------------------------------ fetch

def fetch(council, typename, dto, myorders, scheme, maxfeatures=5000):
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": typename,
        "maxfeatures": maxfeatures,
        "DTO": dto,
        "MYORDERS": myorders,
        "SCHEME": scheme,
    }
    url = BASE.format(council=council) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"  {typename}: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        print(f"  {typename}: {e}")
    return None


# ------------------------------------------------- OSGB36 -> WGS84
# Pure Python, no pyproj. Transverse Mercator inverse on the Airy 1830
# ellipsoid, then a Helmert transform to WGS84. Accurate to a few metres,
# which is ample for a street-level marker.

def osgb_to_wgs84(E, N):
    a, b = 6377563.396, 6356256.909           # Airy 1830
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2)
    N0, E0 = -100000.0, 400000.0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)

    lat = lat0
    M = 0.0
    while abs(N - N0 - M) >= 0.00001:
        lat += (N - N0 - M) / (a * F0)
        Ma = (1 + n + 1.25 * n**2 + 1.25 * n**3) * (lat - lat0)
        Mb = (3*n + 3*n**2 + 2.625*n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (1.875*n**2 + 1.875*n**3) * math.sin(2*(lat - lat0)) * math.cos(2*(lat + lat0))
        Md = (35/24) * n**3 * math.sin(3*(lat - lat0)) * math.cos(3*(lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)

    sl = math.sin(lat)
    nu = a * F0 / math.sqrt(1 - e2 * sl * sl)
    rho = a * F0 * (1 - e2) / (1 - e2 * sl * sl) ** 1.5
    eta2 = nu / rho - 1

    tl = math.tan(lat)
    tl2, tl4, tl6 = tl**2, tl**4, tl**6
    sec = 1 / math.cos(lat)
    VII = tl / (2 * rho * nu)
    VIII = tl / (24 * rho * nu**3) * (5 + 3*tl2 + eta2 - 9*tl2*eta2)
    IX = tl / (720 * rho * nu**5) * (61 + 90*tl2 + 45*tl4)
    X = sec / nu
    XI = sec / (6 * nu**3) * (nu / rho + 2*tl2)
    XII = sec / (120 * nu**5) * (5 + 28*tl2 + 24*tl4)
    XIIA = sec / (5040 * nu**7) * (61 + 662*tl2 + 1320*tl4 + 720*tl6)

    dE = E - E0
    lat_a = lat - VII*dE**2 + VIII*dE**4 - IX*dE**6
    lon_a = lon0 + X*dE - XI*dE**3 + XII*dE**5 - XIIA*dE**7

    # Helmert: OSGB36 -> WGS84
    H = 0.0
    sl, cl = math.sin(lat_a), math.cos(lat_a)
    slo, clo = math.sin(lon_a), math.cos(lon_a)
    v = a / math.sqrt(1 - e2 * sl * sl)
    x1 = (v + H) * cl * clo
    y1 = (v + H) * cl * slo
    z1 = ((1 - e2) * v + H) * sl

    tx, ty, tz = -446.448, 125.157, -542.060
    s = 20.4894e-6
    rx = math.radians(-0.1502 / 3600)
    ry = math.radians(-0.2470 / 3600)
    rz = math.radians(-0.8421 / 3600)

    x2 = tx + x1*(1+s) - y1*rz + z1*ry
    y2 = ty + x1*rz + y1*(1+s) - z1*rx
    z2 = tz - x1*ry + y1*rx + z1*(1+s)

    a2, b2 = 6378137.000, 6356752.3142        # WGS84
    e22 = 1 - (b2*b2)/(a2*a2)
    p = math.sqrt(x2*x2 + y2*y2)
    latn = math.atan2(z2, p * (1 - e22))
    for _ in range(12):
        v2 = a2 / math.sqrt(1 - e22 * math.sin(latn)**2)
        latn = math.atan2(z2 + e22 * v2 * math.sin(latn), p)
    lonn = math.atan2(y2, x2)
    return round(math.degrees(latn), 6), round(math.degrees(lonn), 6)


# ------------------------------------------------------------------ parse

GML = "{http://www.opengis.net/gml}"


def coords_of(feature):
    """Every coordinate pair in a feature's geometry, as (E, N)."""
    pts = []
    for tag in ("posList", "pos"):
        for el in feature.iter(GML + tag):
            nums = [float(x) for x in (el.text or "").split()]
            pts += list(zip(nums[0::2], nums[1::2]))
    return pts


def parts_of(feature):
    """
    Geometry as a list of parts, each a list of (E, N). A multi-part line
    keeps its parts separate so the map doesn't join unconnected stretches
    of kerb with a phantom segment across the borough.
    """
    parts = []
    for el in feature.iter():
        tag = el.tag.replace(GML, "")
        if tag in ("posList", "pos"):
            nums = [float(x) for x in (el.text or "").split()]
            pair = list(zip(nums[0::2], nums[1::2]))
            if pair:
                parts.append(pair)
    return parts


def geom_type_of(feature):
    for el in feature.iter():
        tag = el.tag.replace(GML, "")
        if tag in ("LineString", "MultiLineString", "Curve"):
            return "line"
        if tag in ("Polygon", "MultiSurface", "Surface"):
            return "area"
        if tag in ("Point", "MultiPoint"):
            return "point"
    return "unknown"


def parse(xml_text, layer):
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  {layer}: unparseable XML ({e})")
        return []

    rows = []
    for fm in root.findall("gml:featureMember", NS):
        feat = list(fm)[0] if list(fm) else None
        if feat is None:
            continue
        rec = {"layer": layer}
        for child in feat:
            tag = child.tag.split("}")[-1]
            if tag in ("boundedBy", "msGeometry"):
                continue
            rec[tag] = (child.text or "").strip()
        pts = coords_of(feat)
        if pts:
            e = sum(p[0] for p in pts) / len(pts)
            n = sum(p[1] for p in pts) / len(pts)
            rec["easting"], rec["northing"] = round(e, 2), round(n, 2)
            rec["lat"], rec["lon"] = osgb_to_wgs84(e, n)
            rec["n_points"] = len(pts)
            # Full geometry, converted, for drawing the actual stretch of
            # kerb rather than a pin near it.
            rec["geom_type"] = geom_type_of(feat)
            rec["geometry"] = [
                [[round(x, 6), round(y, 6)]
                 for x, y in (osgb_to_wgs84(pe, pn)[::-1] for pe, pn in part)]
                for part in parts_of(feat)
            ]
        rows.append(rec)
    return rows


# ----------------------------------------------------------------- output

FIELDS = [
    "scheme_ref", "scheme_id", "order_id", "status", "days_left",
    "date_advert", "date_close", "street_name", "side_of_road", "locality",
    "restriction", "order_type", "pre_description", "post_description",
    "times_of_enforcement", "schedule", "entry_type", "order_doc",
    "docpath", "lat", "lon", "easting", "northing", "layer", "item_ref",
    "ogc_fid",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--council", default="hackney")
    ap.add_argument("--myorders", default="1",
                    help="MYORDERS value. Must be present or the layer "
                         "returns nothing; '1' appears to return everything.")
    ap.add_argument("--dto", default=None, help="DTO date, default today")
    ap.add_argument("--schemes", default="600-700",
                    help="Scheme ids to sweep, e.g. '600-700' or '640,641'. "
                         "SCHEME is a required filter: without it the layer "
                         "returns nothing.")
    args = ap.parse_args()

    today = dt.date.today()
    dto = args.dto or today.isoformat()
    out = pathlib.Path("traffic_probe")
    out.mkdir(exist_ok=True)

    # Which scheme ids to try.
    ids = []
    for part in args.schemes.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            ids += list(range(int(lo), int(hi) + 1))
        elif part:
            ids.append(int(part))

    # Sweep: one cheap probe per id against the line layer.
    print(f"sweeping {len(ids)} scheme ids for live data...")
    live = []
    for i, sid in enumerate(ids, 1):
        xml_text = fetch(args.council, "pordersl", dto, args.myorders, sid,
                         maxfeatures=1)
        rows = parse(xml_text, "pordersl")
        if rows:
            ref = rows[0].get("order_ref", "?")
            close = rows[0].get("date_close", "?")
            print(f"  SCHEME={sid}: {ref}  closes {close}")
            live.append(sid)
        if i % 25 == 0:
            print(f"    ...{i}/{len(ids)} probed, {len(live)} live so far")

    if not live:
        print("\nNo scheme ids in that range returned data.")
        print("Widen the range, or read the SCHEME value the app sends in")
        print("DevTools and sweep around it.")
        return

    print(f"\n{len(live)} live schemes. Pulling all layers for each...")
    all_rows = []
    for sid in live:
        for layer in LAYERS:
            xml_text = fetch(args.council, layer, dto, args.myorders, sid)
            if xml_text:
                (out / f"{layer}-{sid}.xml").write_text(xml_text)
            rows = parse(xml_text, layer)
            for r in rows:
                r["queried_scheme"] = sid
            all_rows += rows
        print(f"  SCHEME={sid}: {sum(1 for r in all_rows if r.get('queried_scheme') == sid)} features")

    if not all_rows:
        print("\nNothing returned.")
        return

    # Derive status at RUN time, never at gather time.
    for r in all_rows:
        close = r.get("date_close") or ""
        try:
            cd = dt.date.fromisoformat(close[:10])
            r["days_left"] = (cd - today).days
            r["status"] = "open to objection" if cd >= today else "closed"
        except ValueError:
            r["days_left"] = ""
            r["status"] = "no closing date published"
        r["scheme_ref"] = r.get("order_ref", "")

    loc_path = out / f"traffic-locations-{today}.csv"
    with loc_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(all_rows, key=lambda x: (x["scheme_ref"],
                                                 x.get("street_name", ""))):
            w.writerow(r)

    # GeoJSON, for the map. Coordinates are [lon, lat], WGS84.
    features = []
    for r in all_rows:
        geom = r.get("geometry") or []
        if not geom:
            continue
        gt = r.get("geom_type")
        if gt == "point":
            g = {"type": "Point", "coordinates": geom[0][0]}
        elif gt == "area":
            g = {"type": "Polygon", "coordinates": geom}
        elif len(geom) == 1:
            g = {"type": "LineString", "coordinates": geom[0]}
        else:
            g = {"type": "MultiLineString", "coordinates": geom}
        features.append({
            "type": "Feature",
            "geometry": g,
            "properties": {k: v for k, v in r.items()
                           if k not in ("geometry",) and not isinstance(v, list)},
        })
    geo_path = out / f"traffic-geometry-{today}.geojson"
    geo_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, default=str))
    print(f"\n  {len(features)} features with geometry -> {geo_path}")

    schemes = collections.defaultdict(list)
    for r in all_rows:
        schemes[r["scheme_ref"]].append(r)

    sch_path = out / f"traffic-schemes-{today}.csv"
    with sch_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scheme_ref", "scheme_id", "locations", "date_advert",
                    "date_close", "status", "days_left", "order_doc",
                    "restrictions"])
        for ref, rs in sorted(schemes.items()):
            first = rs[0]
            kinds = sorted({r.get("restriction", "") for r in rs if r.get("restriction")})
            w.writerow([ref, first.get("scheme_id", ""), len(rs),
                        first.get("date_advert", ""), first.get("date_close", ""),
                        first["status"], first["days_left"],
                        first.get("order_doc", ""), "; ".join(kinds)[:200]])

    print("\n" + "=" * 70)
    print(f"{len(all_rows)} locations across {len(schemes)} schemes")
    print("=" * 70)
    openn = [s for s, rs in schemes.items() if rs[0]["status"] == "open to objection"]
    print(f"  open to objection today: {len(openn)}")
    for ref, rs in sorted(schemes.items(),
                          key=lambda kv: (kv[1][0]["status"], kv[0])):
        f0 = rs[0]
        print(f"  {f0['status']:<26} {ref[:44]:<46} {len(rs):>4} locations  "
              f"{f0.get('date_advert','?')} to {f0.get('date_close','?')}")
    print(f"\n  -> {loc_path}\n  -> {sch_path}")
    print("\n  Status is computed now, not when the data was gathered, so a")
    print("  closed consultation cannot be published as open.")


if __name__ == "__main__":
    main()
