#!/usr/bin/env python3
"""
make_roads.py

Cuts Hackney out of OS Open Roads and writes hackney-roads.json for the map
card. Run it once; the output is small and the 606 MB download can then be
deleted.

    python3 make_roads.py path/to/TQ_RoadLink.shp

If you give it a directory it will find the TQ RoadLink shapefile inside:

    python3 make_roads.py ~/Downloads/oproad_essh_gb

OS Open Roads is British National Grid, the same grid as the ward
boundaries and the traffic data, so nothing is reprojected.

Standard library only.
"""

import argparse
import collections
import glob
import json
import math
import os
import struct
import sys

# Hackney's extent from the ward boundaries, with 400 m of margin so roads
# do not stop dead at the border.
MINX, MINY, MAXX, MAXY = 531080, 181440, 538041, 188727

# OS Open Roads describes a road twice. 'class' carries the formal
# classification, which is only meaningful for numbered roads; 'function'
# describes what the road actually is, and is where residential streets
# live. A road's weight is the higher of the two. Anything scoring 0 is
# dropped.
CLASS_WEIGHT = {
    "Motorway": 4,
    "A Road": 3,
    "B Road": 2,
    "Classified Unnumbered": 1,
    "Unclassified": 1,
    "Unknown": 0,
    "Not Classified": 0,
}
FUNCTION_WEIGHT = {
    "Motorway": 4,
    "A Road": 3,
    "B Road": 2,
    "Minor Road": 1,
    "Local Road": 1,
    "Local Access Road": 0,          # cul-de-sacs, estate spurs
    "Restricted Local Access Road": 0,
    "Secondary Access Road": 0,
    "Restricted Secondary Access Road": 0,
}
WEIGHT = dict(CLASS_WEIGHT)          # used to sniff out the class column


# ------------------------------------------------------- shapefile reading

def read_dbf(path):
    f = open(path, "rb")
    hdr = f.read(32)
    nrec, hlen, rlen = struct.unpack("<I H H", hdr[4:12])
    nfields = (hlen - 33) // 32
    fields = []
    for _ in range(nfields):
        d = f.read(32)
        fields.append((d[:11].split(b"\0")[0].decode("latin-1"),
                       chr(d[11]), d[16]))
    f.read(1)
    rows = []
    for _ in range(nrec):
        rec = f.read(rlen)
        if not rec or rec[:1] == b"\x1a":
            break
        off, r = 1, {}
        for name, _t, flen in fields:
            r[name] = rec[off:off+flen].decode("latin-1").strip()
            off += flen
        rows.append(r)
    f.close()
    return [n for n, _, _ in fields], rows


def read_shp_lines(path):
    """Polylines only. Returns a list of parts per record."""
    f = open(path, "rb")
    f.seek(24)
    flen = struct.unpack(">I", f.read(4))[0] * 2
    f.seek(100)
    out = []
    while f.tell() < flen:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        _num, clen = struct.unpack(">II", hdr)
        data = f.read(clen * 2)
        stype = struct.unpack("<I", data[:4])[0]
        if stype not in (3, 13, 23):          # polyline, Z, M
            out.append([])
            continue
        nparts, npoints = struct.unpack("<II", data[36:44])
        parts = struct.unpack("<%dI" % nparts, data[44:44+4*nparts])
        pb = 44 + 4*nparts
        pts = struct.unpack("<%dd" % (2*npoints), data[pb:pb+16*npoints])
        rings = []
        for i, st in enumerate(parts):
            en = parts[i+1] if i+1 < nparts else npoints
            rings.append([(pts[2*j], pts[2*j+1]) for j in range(st, en)])
        out.append(rings)
    f.close()
    return out


# -------------------------------------------------------------- processing

def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    def d(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2-x1, y2-y1
        if dx == 0 and dy == 0:
            return math.dist(p, a)
        t = max(0, min(1, ((x-x1)*dx + (y-y1)*dy) / (dx*dx + dy*dy)))
        return math.dist(p, (x1 + t*dx, y1 + t*dy))
    dmax, idx = 0, 0
    for i in range(1, len(pts)-1):
        dd = d(pts[i], pts[0], pts[-1])
        if dd > dmax:
            dmax, idx = dd, i
    if dmax > eps:
        return rdp(pts[:idx+1], eps)[:-1] + rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def clip(part):
    """Keep only the runs of a line that fall inside the box."""
    runs, cur = [], []
    for x, y in part:
        if MINX <= x <= MAXX and MINY <= y <= MAXY:
            cur.append((x, y))
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [(x, y)] if not cur else []
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def find_shp(target):
    if os.path.isfile(target):
        return target
    pats = [os.path.join(target, "**", "TQ_RoadLink.shp"),
            os.path.join(target, "**", "*RoadLink.shp")]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="TQ_RoadLink.shp, or the unzipped folder")
    ap.add_argument("--tolerance", type=float, default=4.0,
                    help="simplification in metres (default 4)")
    ap.add_argument("--out", default="hackney-roads.json")
    args = ap.parse_args()

    shp = find_shp(args.path)
    if not shp:
        print("Couldn't find a RoadLink shapefile there.")
        print("Point me at TQ_RoadLink.shp, or at the unzipped folder.")
        return 1
    print(f"reading {shp}")

    dbf = shp[:-4] + ".dbf"
    cols, rows = ([], [])
    if os.path.exists(dbf):
        cols, rows = read_dbf(dbf)
        print(f"attributes: {cols}")

    # Which column holds the road class? Match on its values, since
    # shapefile field names are truncated to ten characters.
    classcol = next((c for c in cols
                     if {r.get(c, "") for r in rows[:4000]} & {"A Road", "B Road"}), None)
    funccol = next((c for c in cols
                    if {r.get(c, "") for r in rows[:4000]} & {"Minor Road", "Local Road"}), None)
    namecol = next((c for c in cols if c.lower().startswith("name1")), None)
    print(f"class column: {classcol}   function column: {funccol}   "
          f"name column: {namecol}")
    if funccol:
        seen = collections.Counter(r.get(funccol, "") for r in rows)
        unknown = [v for v in seen if v and v not in FUNCTION_WEIGHT]
        if unknown:
            print(f"  unweighted function values (dropped): {unknown}")

    shapes = read_shp_lines(shp)
    print(f"{len(shapes)} road links in TQ")

    kept = collections.Counter()
    feats = []
    for i, parts in enumerate(shapes):
        attrs = rows[i] if i < len(rows) else {}
        cls = attrs.get(classcol, "") if classcol else ""
        fun = attrs.get(funccol, "") if funccol else ""
        w = max(CLASS_WEIGHT.get(cls, 0), FUNCTION_WEIGHT.get(fun, 0))
        if w == 0:
            continue
        label = cls if CLASS_WEIGHT.get(cls, 0) >= FUNCTION_WEIGHT.get(fun, 0) and cls else fun
        for part in parts:
            for run in clip(part):
                s = rdp(run, args.tolerance)
                if len(s) < 2:
                    continue
                feats.append({
                    "w": w,
                    "n": (attrs.get(namecol) or "") if namecol else "",
                    "p": [[round(x, 1), round(y, 1)] for x, y in s],
                })
                kept[label or "?"] += 1

    json.dump({"crs": "EPSG:27700",
               "source": "OS Open Roads, Ordnance Survey",
               "licence": "Open Government Licence v3",
               "bbox": [MINX, MINY, MAXX, MAXY],
               "roads": feats}, open(args.out, "w"))

    size = os.path.getsize(args.out)
    pts = sum(len(f["p"]) for f in feats)
    print(f"\nkept {len(feats)} segments, {pts} points")
    for c, n in kept.most_common():
        print(f"  {c:<30} {n}")
    print(f"\n-> {args.out}  ({size//1024} kB)")
    if size > 900_000:
        print("That is on the large side for a page embed. Rerun with a")
        print("bigger --tolerance, say 8, to cut it down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
