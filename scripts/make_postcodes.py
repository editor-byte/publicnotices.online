#!/usr/bin/env python3
"""
make_postcodes.py

Pulls the postcode centroids covering Hackney out of OS Code-Point Open and
writes hackney-postcodes.json, so planning applications can be placed on
the map by postcode — the same convention your planning roundups already
use.

    python3 make_postcodes.py ~/Downloads/codepo_gb

Point it at the unzipped Code-Point Open folder and it finds the per-area
CSVs itself. Only postcodes whose centroid falls inside Hackney plus a
small margin are kept, so the output is small.

Code-Point Open is British National Grid, like everything else here.

Standard library only.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys

# Hackney plus margin, matching make_roads.py
MINX, MINY, MAXX, MAXY = 531080, 181440, 538041, 188727

# Code-Point Open has no header row. These are the first four columns.
POSTCODE, QUALITY, EASTING, NORTHING = 0, 1, 2, 3

# Positional quality: 10 is a real property-level centroid, 90 means the
# postcode has no location and the value is a placeholder.
BAD_QUALITY = {"90"}


def normalise(pc):
    """'E8  1DY' -> 'E8 1DY'. Code-Point pads the outward code to 4 chars."""
    pc = (pc or "").strip().upper().replace('"', "")
    pc = re.sub(r"\s+", "", pc)
    if len(pc) < 5:
        return pc
    return pc[:-3] + " " + pc[-3:]


def find_csvs(target):
    if os.path.isfile(target):
        return [target]
    for pat in ("**/Data/CSV/*.csv", "**/CSV/*.csv", "**/*.csv"):
        hits = sorted(glob.glob(os.path.join(target, pat), recursive=True))
        if hits:
            return hits
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="unzipped Code-Point Open folder, or one CSV")
    ap.add_argument("--out", default="hackney-postcodes.json")
    args = ap.parse_args()

    files = find_csvs(args.path)
    if not files:
        print("No Code-Point CSVs found there.")
        print("Point me at the unzipped codepo_gb folder.")
        return 1

    # Only the postcode areas that reach into Hackney.
    wanted = {"e", "ec", "n", "nw", "ig", "rm"}
    files = [f for f in files
             if os.path.basename(f).rsplit(".", 1)[0].lower() in wanted] or files
    print(f"reading {len(files)} area file(s): "
          f"{', '.join(os.path.basename(f) for f in files)}")

    out, scanned, dropped = {}, 0, 0
    for path in files:
        with open(path, newline="", encoding="latin-1") as f:
            for row in csv.reader(f):
                if len(row) <= NORTHING:
                    continue
                scanned += 1
                try:
                    e = float(row[EASTING]); n = float(row[NORTHING])
                except ValueError:
                    continue
                if not (MINX <= e <= MAXX and MINY <= n <= MAXY):
                    continue
                if row[QUALITY].strip() in BAD_QUALITY:
                    dropped += 1
                    continue
                out[normalise(row[POSTCODE])] = [round(e, 1), round(n, 1)]

    # Sector centroids (the "N16 0" part), as a fallback for postcodes that
    # Code-Point does not hold — newly created ones, or errors in a notice.
    # Accurate to a few hundred metres, so anything placed this way must be
    # labelled approximate.
    buckets = {}
    for pc, (e, n) in out.items():
        sector = pc.split(" ")[0] + " " + pc.split(" ")[1][0]
        buckets.setdefault(sector, []).append((e, n))
    sectors = {k: [round(sum(p[0] for p in v)/len(v), 1),
                   round(sum(p[1] for p in v)/len(v), 1)]
               for k, v in buckets.items()}

    json.dump({"crs": "EPSG:27700",
               "source": "OS Code-Point Open, Ordnance Survey",
               "licence": "Open Government Licence v3",
               "bbox": [MINX, MINY, MAXX, MAXY],
               "postcodes": out,
               "sectors": sectors}, open(args.out, "w"))
    print(f"also stored {len(sectors)} sector centroids as a fallback")

    print(f"\nscanned {scanned} postcodes, kept {len(out)} in the Hackney box")
    if dropped:
        print(f"  dropped {dropped} with no reliable position")
    print(f"-> {args.out}  ({os.path.getsize(args.out)//1024} kB)")

    # Does it cover the applications we have?
    if os.path.exists("planning-applications.csv"):
        miss, tot = [], 0
        with open("planning-applications.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pc = normalise(r.get("postcode", ""))
                if not pc:
                    continue
                tot += 1
                if pc not in out:
                    miss.append((r["reference"], pc))
        print(f"\nplanning-applications.csv: {tot - len(miss)}/{tot} postcodes matched")
        for ref, pc in miss[:10]:
            sector = pc.split(" ")[0] + " " + pc.split(" ")[1][0] if " " in pc else ""
            if sector in sectors:
                print(f"  {pc} ({ref}) not in Code-Point — will fall back to "
                      f"the {sector} sector centroid, accurate to a few "
                      f"hundred metres")
            else:
                print(f"  {pc} ({ref}) not in Code-Point and no sector match "
                      f"— needs placing by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
