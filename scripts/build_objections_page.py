#!/usr/bin/env python3
"""
build_objections_page.py

One page answering "what can I object to in Hackney right now" — planning
applications and road changes together, each with the deadline that
actually applies to it.

    python3 traffic_ingest.py
    python3 parse_planning.py hackney-planning-embed_*.html
    python3 build_objections_page.py

Inputs, all British National Grid:
  hackney-wards.json          21 wards, GLA/ONS
  hackney-roads.json          street network, OS Open Roads (optional)
  hackney-postcodes.json      postcode centroids, OS Code-Point Open
  planning-applications.csv   from parse_planning.py
  traffic_probe/traffic-geometry-*.geojson   from traffic_ingest.py

Output: objections-card.html, a self-contained Ghost HTML card.

Only items still open for objection are mapped. Closed ones stay in the
List view, faded. Status is recomputed in the reader's browser, so a page
that has not been rebuilt still tells the truth about what has closed.

Standard library only.
"""

import argparse
import collections
import csv
import datetime as dt
import glob
import html
import json
import math
import pathlib
import re
import sys

W, H, PAD = 720, 760, 16
MIN_DRAW = 9.0

# Planning categories keep the colours readers already see in the roundups.
# Taken from the Hackney Citizen's own planning and traffic cards, so the
# portal reads as the same publication rather than a separate product.
CAT_COLOUR = {
    "Extensions and alterations": "#6f6353",   # taupe
    "Listed building consent": "#7d2436",      # oxblood
    "Change of use": "#a2542a",                # rust
    "Signs and shopfronts": "#b06a1f",         # burnt ochre
    "New homes": "#5d6b33",                    # olive
    "Major development": "#324028",            # deep forest
    "Opening hours": "#c08a2e",                # mustard
    "Infrastructure": "#928a79",               # stone
}
CAT_FALLBACK = "#6f6353"
# Road scheme colours, as the Hackney Citizen's traffic card uses them.
TRAFFIC_COLOUR = {
    "parking": "#8a5a2b",   # permit and pay bays
    "hangar": "#1f6f8b",    # cycle hangars and stands
    "hire": "#2f6b45",      # cycle hire bays
    "ev": "#6b3f8f",        # charging points
    "scheme": "#a8322a",    # street layout and everything else
}
TRAFFIC_LABEL = {
    "parking": "Parking bays", "hangar": "Cycle hangars",
    "hire": "Cycle hire", "ev": "EV charging", "scheme": "Street schemes",
}

# Sites with no postcode at all. Add a British National Grid easting and
# northing to put one on the map; otherwise it is listed without a pin.
# The Citizen's own roundup for each notice, so a reader can get the full
# write-up rather than only the council's register entry. One line per
# fortnight. Until there are per-notice pages on the portal itself, this is
# the "more information" link.
ROUNDUP = {
    "2026-09-17": "https://hackneycitizen.co.uk/2026/09/19/planning-applications-near-you/",
    "2026-10-01": "https://hackneycitizen.co.uk/2026/10/05/planning-applications-near-you-051026/",
    "2026-10-15": "https://hackneycitizen.co.uk/2026/10/17/planning-applications-near-you-171026/",
}
TRAFFIC_ROUNDUP = "https://hackneycitizen.co.uk/2026/10/17/road-changes-near-you-171026/"
TRAFFWEB = "https://hackney.traffweb.app/traffweb/2/PublicConsultation"

MANUAL_PLACE = {
    # "2026/1916": (533250, 187450),   # Woodberry Down Phase 5
}


# ------------------------------------------------------------- projection

# Glyphs lifted from the Hackney Citizen's own traffic card, so a reader
# meets the same symbols here as in the paper's roundups. Drawn on a
# 24x24 grid.
GLYPH = {
    "ev": '<path d="M13.6 2.4 5.2 13.4h5.1l-1.3 8.2 8.6-11.3h-5.2z"/>',
    "parking": '<path d="M8.4 4.6h5.1c3.2 0 5.1 1.9 5.1 4.9 0 3.1-1.9 5-5.1 '
               '5h-1.8v4.9H8.4zm3.3 7.1h1.5c1.5 0 2.3-.8 2.3-2.2 0-1.4-.8-2.2'
               '-2.3-2.2h-1.5z"/>',
    "hire": '<circle cx="6.4" cy="15.6" r="3.9" fill="none" stroke="currentColor"'
            ' stroke-width="1.7"/><circle cx="17.6" cy="15.6" r="3.9" fill="none"'
            ' stroke="currentColor" stroke-width="1.7"/><path d="M6.4 15.6 11 '
            '8.4h5.2l1.4 7.2" fill="none" stroke="currentColor" stroke-width="1.7"'
            ' stroke-linejoin="round"/><rect x="9.6" y="6.4" width="5.6" '
            'height="1.7" rx=".8"/>',
    "hangar": '<path d="M12 4.2 21 11.2v1.5H3v-1.5z"/><rect x="5" y="13.4" '
              'width="14" height="5.4" rx="1.2" fill="none" stroke="currentColor"'
              ' stroke-width="1.6"/><circle cx="9" cy="16.1" r="1.5"/>'
              '<circle cx="15" cy="16.1" r="1.5"/>',
    "scheme": '<path d="M9 3h6l1.6 18h-3.3L12 8l-1.3 13H7.4z"/><rect x="11.2" '
              'y="6" width="1.6" height="3"/><rect x="11.2" y="11" width="1.6" '
              'height="3"/><rect x="11.2" y="16" width="1.6" height="3"/>',
    # A glyph per planning category, drawn in the same language as the
    # traffic symbols above: solid shapes, no fine detail, legible at 14px.
    "house": '<path d="M12 3.4 4 10.2v10.4h6.4v-5.2h3.2v5.2H20V10.2z"/>',
    "listed": '<rect x="4.6" y="4.4" width="14.8" height="2.6"/>'
              '<rect x="7" y="7.6" width="2.4" height="10.4"/>'
              '<rect x="10.8" y="7.6" width="2.4" height="10.4"/>'
              '<rect x="14.6" y="7.6" width="2.4" height="10.4"/>'
              '<rect x="3.6" y="18.6" width="16.8" height="2.6"/>',
    "shopfront": '<path d="M3.4 4.6h17.2l1.4 4.4H2z"/>'
                 '<rect x="4.6" y="10.6" width="6" height="9.8"/>'
                 '<rect x="13" y="10.6" width="6.4" height="5.4"/>',
    "newhomes": '<path d="M10.4 7 3 13.2v8.2h14.8v-8.2z"/>'
                '<path d="M17.6 2.6h2.4v3h3v2.4h-3v3h-2.4v-3h-3V5.6h3z"/>',
    "changeuse": '<path d="M4 9.2h11.4V6l5 4.6-5 4.6v-3.2H4z"/>'
                 '<path d="M20 15.4H8.6v3.2l-5-4.6 5-4.6v3.2H20z"/>',
    "crane": '<rect x="3.4" y="3.6" width="17.2" height="2.4"/>'
             '<rect x="10.6" y="3.6" width="2.4" height="15.4"/>'
             '<rect x="3.4" y="6" width="3" height="3"/>'
             '<rect x="17.2" y="6" width="1.4" height="5.4"/>'
             '<rect x="6.6" y="19" width="10.8" height="2.4"/>',
    "pylon": '<path d="M12 2.8 5.2 21.2h2.5l1-2.8h6.6l1 2.8h2.5z"/>'
             '<rect x="8.2" y="9.4" width="7.6" height="1.5"/>'
             '<rect x="7" y="13.6" width="10" height="1.5"/>',
    "clock": '<circle cx="12" cy="12" r="8.6" fill="none" '
             'stroke="currentColor" stroke-width="2.2"/>'
             '<path d="M12 6.6v6l3.8 2.3" fill="none" stroke="currentColor" '
             'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
}


CAT_GLYPH = {
    "Extensions and alterations": "house",
    "Listed building consent": "listed",
    "Signs and shopfronts": "shopfront",
    "New homes": "newhomes",
    "Change of use": "changeuse",
    "Major development": "crane",
    "Infrastructure": "pylon",
    "Opening hours": "clock",
}


def glyph_for(texts):
    """Pick the symbol that best describes what an order changes. Most
    specific wins: a scheme that adds a charging point is an EV scheme even
    though it also moves a permit bay."""
    blob = " ".join(texts).lower()
    if "electric vehicle" in blob or "charging" in blob:
        return "ev"
    if "cycle hire" in blob:
        return "hire"
    if "cycle" in blob:
        return "hangar"
    if "permit" in blob or "bay" in blob or "chargeable" in blob:
        return "parking"
    return "scheme"


def wgs84_to_osgb(lat_deg, lon_deg):
    a2, b2 = 6378137.000, 6356752.3142
    e22 = 1 - (b2*b2)/(a2*a2)
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    v = a2 / math.sqrt(1 - e22*math.sin(lat)**2)
    x1 = v*math.cos(lat)*math.cos(lon)
    y1 = v*math.cos(lat)*math.sin(lon)
    z1 = ((1-e22)*v)*math.sin(lat)
    tx, ty, tz = 446.448, -125.157, 542.060
    s = -20.4894e-6
    rx = math.radians(0.1502/3600); ry = math.radians(0.2470/3600)
    rz = math.radians(0.8421/3600)
    x2 = tx + x1*(1+s) - y1*rz + z1*ry
    y2 = ty + x1*rz + y1*(1+s) - z1*rx
    z2 = tz - x1*ry + y1*rx + z1*(1+s)
    a, b = 6377563.396, 6356256.909
    e2 = 1 - (b*b)/(a*a)
    p = math.sqrt(x2*x2 + y2*y2)
    latn = math.atan2(z2, p*(1-e2))
    for _ in range(12):
        vn = a/math.sqrt(1 - e2*math.sin(latn)**2)
        latn = math.atan2(z2 + e2*vn*math.sin(latn), p)
    lonn = math.atan2(y2, x2)
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2)
    N0, E0 = -100000.0, 400000.0
    n = (a-b)/(a+b)
    sl = math.sin(latn)
    nu = a*F0/math.sqrt(1-e2*sl*sl)
    rho = a*F0*(1-e2)/(1-e2*sl*sl)**1.5
    eta2 = nu/rho - 1
    Ma = (1+n+1.25*n**2+1.25*n**3)*(latn-lat0)
    Mb = (3*n+3*n**2+2.625*n**3)*math.sin(latn-lat0)*math.cos(latn+lat0)
    Mc = (1.875*n**2+1.875*n**3)*math.sin(2*(latn-lat0))*math.cos(2*(latn+lat0))
    Md = (35/24)*n**3*math.sin(3*(latn-lat0))*math.cos(3*(latn+lat0))
    M = b*F0*(Ma-Mb+Mc-Md)
    cl, tl = math.cos(latn), math.tan(latn)
    I = M + N0
    II = nu/2*sl*cl
    III = nu/24*sl*cl**3*(5-tl**2+9*eta2)
    IIIA = nu/720*sl*cl**5*(61-58*tl**2+tl**4)
    IV = nu*cl
    V = nu/6*cl**3*(nu/rho-tl**2)
    VI = nu/120*cl**5*(5-18*tl**2+tl**4+14*eta2-58*tl**2*eta2)
    dl = lonn - lon0
    return (E0 + IV*dl + V*dl**3 + VI*dl**5,
            I + II*dl**2 + III*dl**4 + IIIA*dl**6)


def ring_area(ring):
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]; x2, y2 = ring[i+1]
        a += x1*y2 - x2*y1
    return abs(a) / 2


def ring_centroid(ring):
    a = cx = cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]; x2, y2 = ring[i+1]
        f = x1*y2 - x2*y1
        a += f; cx += (x1 + x2)*f; cy += (y1 + y2)*f
    if a == 0:
        return (sum(p[0] for p in ring)/len(ring),
                sum(p[1] for p in ring)/len(ring))
    a *= 0.5
    return cx/(6*a), cy/(6*a)


def inside(pt, ring):
    x, y = pt
    hit = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]; x2, y2 = ring[i+1]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit


def clearest_point(ring, obstacles, halfwidth=34.0, halfheight=11.0, step=7):
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    best, bestscore = None, -1
    for i in range(int((max(xs)-min(xs))/step) + 1):
        for j in range(int((max(ys)-min(ys))/step) + 1):
            p = (min(xs) + i*step, min(ys) + j*step)
            if not inside(p, ring):
                continue
            d = 999.0
            for ox, oy in obstacles:
                d = min(d, math.hypot((p[0]-ox)/halfwidth, (p[1]-oy)/halfheight))
            score = min(d, (min(math.dist(p, v) for v in ring) / 26.0) * 1.3)
            if score > bestscore:
                best, bestscore = p, score
    return best, bestscore


# The zoom at which each class of road gets its name, matching the bands in
# the page's JavaScript.
REVEAL_K = {4: 1.8, 3: 1.8, 2: 2.6, 1: 3.6}
LABEL_PX = 9.0          # rendered font size, in screen pixels
CHAR_W = 0.55           # average glyph width in Lora, as a fraction of em


def label_fits(name, pts, w):
    """
    textPath draws only the part of the text that fits along the path and
    silently clips the rest, so a long name on a short road comes out as
    "ghton Ro". Require the road to be comfortably longer than the label
    will be at the zoom where it first appears.
    """
    k = REVEAL_K.get(w, 3.6)
    text_units = CHAR_W * (LABEL_PX / k) * len(name)
    return seg_len(pts) >= text_units * 1.15


def seg_len(pts):
    return sum(math.dist(pts[i], pts[i+1]) for i in range(len(pts)-1))


def lengthen(pts, minlen):
    total = sum(math.dist(pts[i], pts[i+1]) for i in range(len(pts)-1))
    if total >= minlen:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2-x1, y2-y1
    d = math.hypot(dx, dy) or 1.0
    if dx == 0 and dy == 0:
        dx, dy = 1.0, 0.0
    ux, uy = dx/d, dy/d
    cx, cy = (x1+x2)/2, (y1+y2)/2
    h = minlen/2
    return [(round(cx-ux*h, 1), round(cy-uy*h, 1)),
            (round(cx+ux*h, 1), round(cy+uy*h, 1))]


def declutter(pins, minsep=25.0, rounds=90):
    for _ in range(rounds):
        moved = False
        for i in range(len(pins)):
            for j in range(i+1, len(pins)):
                a, b = pins[i], pins[j]
                dx, dy = b["x"]-a["x"], b["y"]-a["y"]
                d = math.hypot(dx, dy)
                if d < minsep:
                    if d == 0:
                        dx, dy, d = 0.6, -0.8, 1.0
                    push = (minsep - d)/2 + 0.4
                    ux, uy = dx/d, dy/d
                    a["x"] -= ux*push; a["y"] -= uy*push
                    b["x"] += ux*push; b["y"] += uy*push
                    moved = True
        if not moved:
            break
    return pins


def fmt(iso):
    try:
        return dt.date.fromisoformat((iso or "")[:10]).strftime("%-d %B %Y")
    except ValueError:
        return iso or "—"


def norm_pc(pc):
    pc = re.sub(r"\s+", "", (pc or "").upper())
    return pc[:-3] + " " + pc[-3:] if len(pc) >= 5 else pc


def describe_traffic(p):
    kind = (p.get("order_type") or "").strip()
    detail = (p.get("restriction") or p.get("pre_description") or "").strip()
    what = (f"{kind}: {detail}"
            if kind and detail and kind.lower() not in detail.lower()
            else (detail or kind))
    side = (p.get("side_of_road") or "").strip()
    if side and side != "both":
        what += f" ({side} side)"
    return what


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wards", default="hackney-wards.json")
    ap.add_argument("--roads", default="hackney-roads.json")
    ap.add_argument("--postcodes", default="hackney-postcodes.json")
    ap.add_argument("--planning", default="planning-applications.csv")
    ap.add_argument("--traffic", default=None)
    ap.add_argument("--fullpage", action="store_true",
                    help="standalone zoomable page instead of a Ghost card")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    today = dt.date.today()
    iso_today = today.isoformat()

    wards = json.load(open(args.wards))["wards"]
    roads = (json.load(open(args.roads))["roads"]
             if pathlib.Path(args.roads).exists() else [])
    pcdata = (json.load(open(args.postcodes))
              if pathlib.Path(args.postcodes).exists() else {})
    centroids = pcdata.get("postcodes", {})
    sectors = pcdata.get("sectors", {})

    found = sorted(glob.glob("traffic_probe/traffic-geometry-*.geojson"))
    tpath = args.traffic or (found[-1] if found else None)
    tfeats = json.load(open(tpath))["features"] if tpath else []

    plan = []
    if pathlib.Path(args.planning).exists():
        with open(args.planning, newline="", encoding="utf-8") as f:
            plan = list(csv.DictReader(f))

    print(f"{len(plan)} planning applications, {len(tfeats)} traffic "
          f"restrictions, {len(roads)} road segments")

    # --- projection ---
    xs = [p[0] for w in wards for r in w["rings"] for p in r]
    ys = [p[1] for w in wards for r in w["rings"] for p in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = min((W-2*PAD)/(maxx-minx), (H-2*PAD)/(maxy-miny))
    ox = PAD + ((W-2*PAD) - (maxx-minx)*s)/2
    oy = PAD + ((H-2*PAD) - (maxy-miny)*s)/2

    def proj(e, n):
        return round(ox + (e-minx)*s, 1), round(H - oy - (n-miny)*s, 1)

    ward_d = []
    for w in wards:
        for ring in w["rings"]:
            pts = [proj(x, y) for x, y in ring]
            ward_d.append("M" + " L".join(f"{x},{y}" for x, y in pts) + " Z")

    byw = collections.defaultdict(list)
    named = []
    for r in roads:
        pts = [proj(x, y) for x, y in r["p"]]
        if len(pts) < 2:
            continue
        byw[r["w"]].append("M" + " L".join(f"{x},{y}" for x, y in pts))
        if r.get("n"):
            named.append((r["w"], r["n"], pts))
    # Each road is drawn twice: a darker casing, then a lighter fill on top,
    # which is what gives a road an edge rather than looking like a line on
    # a diagram. All casings go down first so junctions knit together
    # instead of showing seams, and heavier roads are drawn last so they sit
    # above the back streets.
    road_svg = "".join(
        f'<path class="rdc rdc{w}" vector-effect="non-scaling-stroke" '
        f'd="{" ".join(byw[w])}"/>' for w in sorted(byw))
    road_svg += "".join(
        f'<path class="rdf rdf{w}" vector-effect="non-scaling-stroke" '
        f'd="{" ".join(byw[w])}"/>' for w in sorted(byw))

    # Street labels, set along the road itself with textPath and revealed as
    # the reader zooms: main roads first, residential streets last. One per
    # name per grid cell, so a long road gets labelled a few times along its
    # length rather than once per segment.
    seen, road_labels = set(), []
    for w, name, pts in sorted(named, key=lambda t: -seg_len(t[2])):
        if not label_fits(name, pts, w):
            continue
        cell = (name.lower(), int(pts[0][0] // 110), int(pts[0][1] // 110))
        if cell in seen:
            continue
        seen.add(cell)
        if pts[-1][0] < pts[0][0]:      # keep text the right way up
            pts = pts[::-1]
        pid = f"r{len(road_labels)}"
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        road_labels.append(
            f'<path id="{pid}" class="rp" fill="none" stroke="none" d="{d}"/>'
            f'<text class="rl rl{w}" dy="-2.5">'
            f'<textPath href="#{pid}" startOffset="50%" text-anchor="middle">'
            f'{html.escape(name.title())}</textPath></text>')
    road_label_svg = "".join(road_labels)
    if roads:
        print(f"{len(road_labels)} street labels from {len(named)} "
              f"named segments ({len(roads)} segments total)")

    pins, detail, pid = [], {}, 0
    plan_sections, traffic_sections = [], []

    # ---------------- planning: one pin per postcode -------------------
    bypc = collections.OrderedDict()
    unplaced = []
    for r in plan:
        pc = norm_pc(r.get("postcode"))
        ref = r["reference"]
        at = None
        if ref in MANUAL_PLACE:
            at, how = MANUAL_PLACE[ref], "manual"
        elif pc in centroids:
            at, how = centroids[pc], "exact"
        else:
            sector = pc.rsplit(" ", 1)[0] + " " + pc.rsplit(" ", 1)[1][0] if " " in pc else ""
            if sector in sectors:
                at, how = sectors[sector], "sector"
        if at is None:
            unplaced.append(r)
            continue
        key = (round(at[0], 1), round(at[1], 1))
        bypc.setdefault(key, {"at": at, "how": how, "rows": []})["rows"].append(r)

    for key, grp in bypc.items():
        rows = grp["rows"]
        live = [r for r in rows if r["comment_deadline"] >= iso_today]
        if not live:
            continue                      # closed: list only
        pid += 1
        px, py = proj(*grp["at"])
        cats = [r["category"] for r in live]
        top = collections.Counter(cats).most_common(1)[0][0]
        colour = CAT_COLOUR.get(top, CAT_FALLBACK)
        deadline = min(r["comment_deadline"] for r in live)
        pins.append({"id": pid, "x": px, "y": py, "tx": px, "ty": py,
                     "n": len(live), "c": colour, "kind": "planning",
                     "close": deadline, "glyph": CAT_GLYPH.get(top, "house")})
        detail[pid] = {
            "kind": "planning",
            "title": live[0]["postcode"] or live[0]["address"][:40],
            "sub": f"{len(live)} planning application"
                   f"{'s' if len(live) != 1 else ''}",
            "approx": grp["how"] != "exact",
            "close": deadline, "closeTxt": fmt(deadline), "colour": colour,
            "items": [{
                "head": f"{r['reference']} · {r['address']}",
                "body": f"{r['category']}"
                        + (f" · {r['designation']}" if r["designation"] else "")
                        + ". " + r["proposal_official"][:260]
                        + ("…" if len(r["proposal_official"]) > 260 else ""),
                "url": r["register_url"],
                "note": ("" if r["on_weekly_list"] == "yes"
                         else "Not yet on the council’s weekly list"),
            } for r in live],
        }

    for r in sorted(plan, key=lambda r: (r["comment_deadline"],
                                         r["postcode_district"], r["reference"])):
        shut = r["comment_deadline"] < iso_today
        link = (f'<a href="{html.escape(r["register_url"])}" rel="noopener">'
                f'{html.escape(r["reference"])}</a>' if r["register_url"]
                else html.escape(r["reference"]) +
                ' <span class="flag">not yet on the council’s weekly list</span>')
        plan_sections.append(
            f'<li class="row" data-close="{r["comment_deadline"]}"'
            f'{" data-shut=1" if shut else ""}>'
            f'<span class="sw" style="background:'
            f'{CAT_COLOUR.get(r["category"], CAT_FALLBACK)}"></span>'
            f'<b>{html.escape(r["address"])}</b> — {link}. '
            f'{html.escape(r["category"])}'
            f'{" · " + html.escape(r["designation"]) if r["designation"] else ""}. '
            f'<span class="dl" data-close="{r["comment_deadline"]}" '
            f'data-open="Comment by {fmt(r["comment_deadline"])}" '
            f'data-closed="Closed {fmt(r["comment_deadline"])}">'
            f'Comment by {fmt(r["comment_deadline"])}</span></li>')

    # ---------------- traffic: pin per street, line per scheme ----------
    schemes = collections.OrderedDict()
    for f in tfeats:
        schemes.setdefault(f["properties"].get("order_ref", "?"), []).append(f)

    lines = []
    for ref, fs in schemes.items():
        close = fs[0]["properties"].get("date_close", "")
        advert = fs[0]["properties"].get("date_advert", "")
        seg, bystreet = [], collections.OrderedDict()
        for f in fs:
            p = f["properties"]
            st = (p.get("street_name") or "").title()
            g = f["geometry"]
            parts = ([g["coordinates"]] if g["type"] == "LineString"
                     else g["coordinates"])
            allp = []
            for part in parts:
                pts = [proj(*wgs84_to_osgb(lat, lon)) for lon, lat in part]
                if len(pts) >= 2:
                    allp += pts
                    seg.append("M" + " L".join(f"{x},{y}" for x, y in
                                               lengthen(pts, MIN_DRAW)))
            rec = bystreet.setdefault(st, {"what": [], "pts": []})
            d = describe_traffic(p)
            if d and d not in rec["what"]:
                rec["what"].append(d)
            rec["pts"] += allp

        if close >= iso_today:
            lines.append(f'<path class="ln" stroke="{TRAFFIC_COLOUR["scheme"]}" '
                         f'd="{" ".join(seg)}"/>')
            for st, rec in bystreet.items():
                if not rec["pts"]:
                    continue
                pid += 1
                cx = sum(p[0] for p in rec["pts"])/len(rec["pts"])
                cy = sum(p[1] for p in rec["pts"])/len(rec["pts"])
                gl = glyph_for(rec["what"])
                pins.append({"id": pid, "x": cx, "y": cy,
                             "tx": round(cx, 1), "ty": round(cy, 1),
                             "n": len(rec["what"]), "c": TRAFFIC_COLOUR[gl],
                             "kind": "traffic", "close": close, "glyph": gl})
                detail[pid] = {
                    "kind": "traffic", "title": st, "sub": ref,
                    "approx": False, "close": close, "closeTxt": fmt(close),
                    "more": TRAFFWEB,
                    "moreTxt": "See this scheme on the council's site",
                    "colour": TRAFFIC_COLOUR[gl],
                    "items": [{"head": "", "body": w, "url": "", "note": ""}
                              for w in rec["what"]],
                }

        rows = "".join(
            f'<li><b>{html.escape(st)}</b> — '
            f'{html.escape("; ".join(r["what"]))}</li>'
            for st, r in bystreet.items())
        shut = close < iso_today
        traffic_sections.append(
            f'<section class="sch" data-close="{close}"'
            f'{" data-shut=1" if shut else ""}>'
            f'<h4><span class="sw" style="background:{TRAFFIC_COLOUR["scheme"]}"></span>'
            f'{html.escape(ref)}</h4>'
            f'<p class="dl" data-close="{close}" '
            f'data-open="Open to objection until {fmt(close)}" '
            f'data-closed="Objections closed {fmt(close)}">'
            f'Open to objection until {fmt(close)}</p>'
            f'<p class="adv">Advertised {fmt(advert)} · '
            f'{len(bystreet)} streets</p><ul>{rows}</ul></section>')

    declutter(pins)

    obstacles = [(p["x"], p["y"]) for p in pins]
    labels = []
    for w in wards:
        big = max(w["rings"], key=ring_area)
        ring = [proj(x, y) for x, y in big]
        pt, score = clearest_point(ring, obstacles)
        if pt is None or score < 0.55:
            pt = proj(*ring_centroid(big))
        px, py = round(pt[0], 1), round(pt[1], 1)
        name = w["name"].replace("&", "&amp;")
        words = name.split()
        if len(name) > 13 and len(words) > 1:
            mid = len(words)//2 + len(words) % 2
            labels.append(f'<text class="wl" x="{px}" y="{py-4}">'
                          f'{" ".join(words[:mid])}<tspan x="{px}" dy="10">'
                          f'{" ".join(words[mid:])}</tspan></text>')
        else:
            labels.append(f'<text class="wl" x="{px}" y="{py}">{name}</text>')

    pin_svg = []
    for p in pins:
        x, y = round(p["x"], 1), round(p["y"], 1)
        lead = (f'<line class="lead" x1="{p["tx"]}" y1="{p["ty"]}" '
                f'x2="{x}" y2="{y}"/>'
                if math.dist((x, y), (p["tx"], p["ty"])) > 3 else "")
        # Every marker is a disc, as on the paper's traffic card.
        r = 11 if (p["n"] > 1 or p.get("glyph")) else 8
        shape = f'<circle r="{r}" fill="{p["c"]}"/>'
        # Roads always show their symbol. A planning marker shows the house
        # unless it stands for several applications, when the count matters
        # more.
        if p["kind"] == "planning" and p["n"] > 1:
            count = f'<text y="4">{p["n"]}</text>'
        elif p.get("glyph"):
            count = (f'<svg class="gly" x="-7" y="-7" width="14" height="14" '
                     f'viewBox="0 0 24 24" aria-hidden="true">'
                     f'<g class="mark">{GLYPH[p["glyph"]]}</g></svg>')
        elif p["n"] > 1:
            count = f'<text y="4">{p["n"]}</text>'
        else:
            count = ""
        pin_svg.append(
            f'{lead}<g class="pin {p["kind"]}" data-id="{p["id"]}" '
            f'data-close="{p["close"]}" data-x="{x}" data-y="{y}" '
            f'transform="translate({x},{y})" '
            f'tabindex="0" role="button">{shape}{count}</g>')

    nplan = sum(1 for r in plan if r["comment_deadline"] >= iso_today)
    ntraffic = sum(1 for fs in schemes.values()
                   if fs[0]["properties"].get("date_close", "") >= iso_today)
    plan_closes = ",".join(r["comment_deadline"] for r in plan
                           if r["comment_deadline"])
    traffic_closes = ",".join(fs[0]["properties"].get("date_close", "")
                              for fs in schemes.values()
                              if fs[0]["properties"].get("date_close"))

    legend = "".join(
        f'<li><span class="sw" style="background:{c}"></span>{html.escape(k)}</li>'
        for k, c in CAT_COLOUR.items()
        if any(r["category"] == k and r["comment_deadline"] >= iso_today
               for r in plan))
    shown = {p["glyph"] for p in pins if p["kind"] == "traffic"}
    legend += "".join(
        f'<li><span class="sw sq" style="background:{TRAFFIC_COLOUR[g]}">'
        f'</span>{TRAFFIC_LABEL[g]}</li>'
        for g in ("parking", "hangar", "hire", "ev", "scheme") if g in shown)

    unplaced_html = ""
    if unplaced:
        rows = "".join(
            f'<li><b>{html.escape(r["address"])}</b> — '
            f'{html.escape(r["reference"])}. {html.escape(r["category"])}. '
            f'Comment by {fmt(r["comment_deadline"])}</li>'
            for r in unplaced if r["comment_deadline"] >= iso_today)
        if rows:
            unplaced_html = (
                '<p class="nopc">Not on the map, because the notice gives no '
                'postcode:</p><ul class="nopc-list">' + rows + "</ul>")

    tmpl = TEMPLATE_PAGE if args.fullpage else TEMPLATE
    outname = args.out or ("objections.html" if args.fullpage
                           else "objections-card.html")
    card = tmpl.format(
        vieww=W, viewh=H, wardpath=" ".join(ward_d), roads=road_svg,
        roadlabels=road_label_svg,
        wardlabels="\n".join(labels), lines="\n".join(lines),
        pins="\n".join(pin_svg), legend=legend,
        planning="\n".join(plan_sections),
        traffic="\n".join(traffic_sections),
        unplaced=unplaced_html,
        detail=json.dumps(detail),
        nplan=nplan, ntraffic=ntraffic,
        plan_closes=plan_closes, traffic_closes=traffic_closes,
        generated=today.strftime("%-d %B %Y"))

    pathlib.Path(outname).write_text(card)
    print(f"\nopen today: {nplan} planning applications, {ntraffic} road schemes")
    print(f"mapped: {len(pins)} pins "
          f"({sum(1 for p in pins if p['kind']=='planning')} planning, "
          f"{sum(1 for p in pins if p['kind']=='traffic')} traffic)")
    if unplaced:
        print(f"not placed: {len(unplaced)} — "
              + ", ".join(r["reference"] for r in unplaced[:6]))
        print("  add a grid reference to MANUAL_PLACE to map them")
    print(f"-> {outname}  ({len(card)//1024} kB)")
    return 0


TEMPLATE = """<!--kg-card-begin: html-->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
.hkob,.hkob *{{box-sizing:border-box}}
.hkob{{
  --ink:#1b1b26; --quiet:#5f5f6d; --paper:#fff; --chip:#f2f1ee;
  --land:#e9e7e0; --wardline:#d7d3c7; --edge:#9b968a; --rule:#e3e1db;
  --brand:#2b2a4a;
  font-family:Lora,Georgia,'Times New Roman',serif; color:var(--ink);
  line-height:1.5; margin:1.6em 0; max-width:1000px;
}}
.hkob h2{{font-size:1.8em;line-height:1.14;margin:0 0 .2em;font-weight:700}}
.hkob h3{{font-size:1.2em;margin:1.6em 0 .5em}}
.hkob .standfirst{{margin:0 0 1.1em;color:var(--quiet)}}
.hkob .bar{{display:flex;gap:.6em;flex-wrap:wrap;align-items:center;margin:0 0 .8em}}
.hkob .search{{flex:1 1 15em;display:flex;align-items:center;gap:.5em;
  border:1px solid var(--rule);border-radius:999px;padding:.55em 1em;
  background:var(--paper)}}
.hkob .search svg{{flex:0 0 auto;width:15px;height:15px}}
.hkob .search input{{border:0;outline:0;font:inherit;font-size:.98em;width:100%;
  background:transparent;color:var(--ink)}}
.hkob .toggle{{display:flex;border:1px solid var(--rule);border-radius:10px;
  overflow:hidden}}
.hkob .toggle button{{font:inherit;font-size:.95em;padding:.5em 1.1em;border:0;
  background:var(--paper);color:var(--ink);cursor:pointer}}
.hkob .toggle button[aria-pressed=true]{{background:var(--brand);color:#fff}}
.hkob .count{{font-size:.95em;color:var(--quiet);padding:.35em 0 .9em;
  border-bottom:1px solid var(--rule);margin-bottom:1em}}
.hkob .count b{{color:var(--ink)}}
.hkob .mapwrap{{position:relative;border:1px solid var(--rule);
  border-radius:14px;overflow:hidden;background:#f7f6f3}}
.hkob .mapwrap svg{{display:block;width:100%;height:auto}}
.hkob .wards{{fill:var(--land);stroke:var(--wardline);stroke-width:.8;
  stroke-linejoin:round}}
.hkob .outline{{fill:none;stroke:var(--edge);stroke-width:2.5;
  stroke-linejoin:round}}
.hkob .veil{{fill:#f7f6f3;opacity:.74;pointer-events:none}}
.hkob .rdc,.hkob .rdf{{fill:none;stroke-linecap:round;stroke-linejoin:round}}
.hkob .rdc1{{stroke:#c3bb9f;stroke-width:1.5}}
.hkob .rdc2{{stroke:#bdae84;stroke-width:2.4}}
.hkob .rdc3{{stroke:#b89a5e;stroke-width:3.4}}
.hkob .rdc4{{stroke:#ab8a4c;stroke-width:4.4}}
.hkob .rdf1{{stroke:#f8f5e9;stroke-width:.85}}
.hkob .rdf2{{stroke:#f4ead0;stroke-width:1.5}}
.hkob .rdf3{{stroke:#eed9a9;stroke-width:2.2}}
.hkob .rdf4{{stroke:#e6c78d;stroke-width:3.0}}
.hkob .wl{{font-size:11px;fill:#6f6a52;text-anchor:middle;
  font-family:Lora,Georgia,serif;paint-order:stroke;stroke:var(--land);
  stroke-width:3px;stroke-linejoin:round;pointer-events:none}}
.hkob .ln{{fill:none;stroke-width:2.5;stroke-linecap:round;opacity:.45}}
.hkob .lead{{stroke:#8d8880;stroke-width:.9}}
.hkob /* Street labels: hidden until the reveal bands below switch them on. */
.rp{{fill:none;stroke:none}}
.rl{{font-family:Lora,Georgia,serif;fill:#5b5b57;font-size:9px;
  paint-order:stroke;stroke:var(--land);stroke-width:2.6px;
  stroke-linejoin:round;pointer-events:none;display:none}}
.rl3,.rl4{{fill:#3d352b}}
svg[data-k="2"] .rl3,svg[data-k="2"] .rl4,
svg[data-k="3"] .rl2,svg[data-k="3"] .rl3,svg[data-k="3"] .rl4,
svg[data-k="4"] .rl{{display:inline}}
.pin{{cursor:pointer}}
.hkob .pin circle,.hkob .pin rect{{stroke:#fff;stroke-width:2}}
.hkob .pin text{{fill:#fff;font-size:11px;font-weight:600;text-anchor:middle;
  font-family:Lora,Georgia,serif;pointer-events:none}}
.hkob .pin:hover circle,.hkob .pin:hover rect{{stroke:var(--ink)}}
.hkob .pin:focus{{outline:none}}
.hkob .pin:focus-visible circle,.hkob .pin:focus-visible rect{{
  stroke:var(--brand);stroke-width:3.5}}
.hkob .card{{position:absolute;left:14px;bottom:14px;width:min(24em,74%);
  background:var(--paper);border-radius:12px;padding:.9em 1em;
  box-shadow:0 6px 22px rgba(0,0,0,.16);font-size:.95em;
  max-height:62%;overflow:auto}}
.hkob .card .tag{{display:inline-block;font-size:.72em;letter-spacing:.06em;
  text-transform:uppercase;background:var(--chip);border-radius:5px;
  padding:.25em .6em;margin-bottom:.5em}}
.hkob .card h4{{margin:0 0 .2em;font-size:1.05em}}
.hkob .card .sub{{margin:0 0 .5em;color:var(--quiet);font-size:.9em}}
.hkob .card .it{{border-top:1px solid var(--rule);padding-top:.5em;
  margin-top:.5em}}
.hkob .card .it b{{display:block;font-size:.93em;margin-bottom:.15em}}
.hkob .card .it span{{font-size:.9em;color:var(--quiet)}}
.hkob .card .close{{margin-top:.6em;font-size:.9em}}
.hkob .hint{{position:absolute;right:14px;top:14px;background:var(--paper);
  border-radius:999px;padding:.4em .9em;font-size:.85em;color:var(--quiet);
  box-shadow:0 2px 8px rgba(0,0,0,.1)}}
.hkob .legend{{list-style:none;display:flex;flex-wrap:wrap;gap:.35em 1.2em;
  padding:.9em 0 0;margin:0;font-size:.88em}}
.hkob .legend li{{display:flex;align-items:center;gap:.45em}}
.hkob .sw{{display:inline-block;width:1.1em;height:.32em;border-radius:2px;
  flex:0 0 auto}}
.hkob .sw.sq{{width:.7em;height:.7em;border-radius:50%}}
.hkob ul.rows{{list-style:none;padding:0;margin:0}}
.hkob .row{{display:flex;gap:.5em;align-items:baseline;padding:.45em 0;
  border-bottom:1px solid var(--rule);font-size:.94em}}
.hkob .row .sw{{margin-top:.45em}}
.hkob .dl{{color:var(--quiet);font-size:.92em;white-space:nowrap}}
.hkob .flag{{color:#8a5a2b;font-size:.9em}}
.hkob .sch{{border-top:1px solid var(--rule);padding-top:.8em;margin-top:1em}}
.hkob .sch h4{{font-size:1.05em;margin:0 0 .25em;display:flex;
  align-items:center;gap:.5em}}
.hkob .sch .adv{{margin:.1em 0 .4em;color:var(--quiet);font-size:.9em}}
.hkob .sch li{{font-size:.92em;margin:.22em 0}}
.hkob .nopc{{margin-top:1.2em;font-size:.93em;color:var(--quiet)}}
.hkob .nopc-list li{{font-size:.93em}}
.hkob [data-shut]{{opacity:.55}}
.hkob .foot{{margin-top:1.4em;color:var(--quiet);font-size:.85em}}
.hkob .hide{{display:none}}
@media (max-width:620px){{
  .hkob .card{{position:static;width:auto;margin:.8em;box-shadow:none;
    border:1px solid var(--rule);max-height:none}}
  .hkob .hint{{display:none}}
}}
</style>
<div class="hkob" id="hkob">
<h2>What you can object to in Hackney right now</h2>
<p class="standfirst">Planning applications and road changes currently open for
 comment or objection, with the deadline for each. Tap a marker for what is
 proposed there. Plain markers are buildings; marked ones are road orders, and
 the symbol shows what each changes. A number means more than one application
 at that spot.</p>

<div class="bar">
  <label class="search">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#5f5f6d"
         stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/>
      <path d="M20 20l-4-4"/></svg>
    <input id="hkob-q" type="search"
           placeholder="Filter by street, postcode or reference…"
           aria-label="Filter by street, postcode or reference">
  </label>
  <div class="toggle" role="group" aria-label="View">
    <button id="hkob-map" aria-pressed="true">Map</button>
    <button id="hkob-list" aria-pressed="false">List</button>
  </div>
</div>

<p class="count"><b id="hkob-n">{nplan} planning applications</b>
 <span id="hkob-t">and {ntraffic} road schemes</span> open now</p>

<div id="hkob-mapview">
  <div class="mapwrap">
    <svg viewBox="0 0 {vieww} {viewh}" role="img"
         aria-label="Map of Hackney and its wards showing planning applications
         and road changes open for objection. Everything shown is also listed
         in text in the List view.">
      <path class="outline" d="{wardpath}"/>
      <path class="wards" d="{wardpath}"/>
{roads}
{roadlabels}
      <path class="veil" fill-rule="evenodd"
            d="M0,0 H{vieww} V{viewh} H0 Z {wardpath}"/>
      <path class="outline" d="{wardpath}"/>
{wardlabels}
{lines}
{pins}
    </svg>
    <div class="hint" id="hkob-hint">Tap a marker</div>
    <div class="card hide" id="hkob-card" aria-live="polite"></div>
  </div>
  <ul class="legend">{legend}</ul>
  {unplaced}
</div>

<div id="hkob-listview" class="hide">
  <h3>Planning applications</h3>
  <ul class="rows">
{planning}
  </ul>
  <h3>Road changes</h3>
{traffic}
  {unplaced}
</div>

<p class="foot">Planning applications advertised by Hackney Council and
 reported in the Hackney Citizen; the deadline is 21 days from the date of the
 notice, which is the only published date by which a comment must be made —
 the council’s own register shows when it intends to decide, not when you can
 object. Road changes from the council’s published traffic order data,
 retrieved {generated}. Boundaries: GLA and ONS London ward boundaries. Roads
 and postcode positions: OS Open Roads and OS Code-Point Open, © Crown
 copyright and database right, Open Government Licence v3. Markers sit at the
 postcode, not the building, and are nudged apart where they would overlap,
 with a hairline back to the true position. Deadlines are checked against your
 device’s clock each time this page loads.</p>
</div>
<script>
(function(){{
  var root=document.getElementById('hkob'); if(!root) return;
  var D={detail};
  var today;
  try{{today=new Intl.DateTimeFormat('en-CA',{{timeZone:'Europe/London',
    year:'numeric',month:'2-digit',day:'2-digit'}}).format(new Date());}}
  catch(e){{var n=new Date();today=n.getFullYear()+'-'+('0'+(n.getMonth()+1)).slice(-2)+'-'+('0'+n.getDate()).slice(-2);}}
  var shut=function(c){{return c && today>c;}};

  Array.prototype.forEach.call(root.querySelectorAll('.dl[data-close]'),function(el){{
    var c=shut(el.getAttribute('data-close'));
    el.textContent=el.getAttribute(c?'data-closed':'data-open');
  }});
  Array.prototype.forEach.call(root.querySelectorAll('[data-close]'),function(el){{
    if(el.classList.contains('dl')) return;
    if(shut(el.getAttribute('data-close'))) el.setAttribute('data-shut','1');
  }});
  Array.prototype.forEach.call(root.querySelectorAll('svg .pin'),function(p){{
    if(shut(p.getAttribute('data-close'))) p.style.display='none';
  }});

  function openCount(csv){{
    return csv.split(',').filter(function(c){{return c && today<=c;}}).length;
  }}
  var np=openCount('{plan_closes}'), nt=openCount('{traffic_closes}');
  document.getElementById('hkob-n').textContent =
    np+(np===1?' planning application':' planning applications');
  document.getElementById('hkob-t').textContent =
    'and '+nt+(nt===1?' road scheme':' road schemes');

  var card=document.getElementById('hkob-card');
  var hint=document.getElementById('hkob-hint');
  function show(id){{
    var d=D[id]; if(!d) return;
    var closed=shut(d.close);
    var items=d.items.map(function(it){{
      var h=it.head?'<b>'+(it.url?'<a href="'+it.url+'" rel="noopener">'+it.head+'</a>':it.head)+'</b>':'';
      var note=it.note?' <i>'+it.note+'</i>':'';
      return '<div class="it">'+h+'<span>'+it.body+note+'</span></div>';
    }}).join('');
    card.innerHTML='<span class="tag">'+
      (closed?'Closed':(d.kind==='planning'?'Planning':'Road change'))+
      '</span><h4>'+d.title+'</h4><p class="sub">'+d.sub+
      (d.approx?' · position approximate':'')+'</p>'+items+
      '<p class="close">'+(closed?'Closed ':'Comment by ')+d.closeTxt+'</p>';
    card.style.borderLeft='4px solid '+d.colour;
    card.classList.remove('hide'); hint.classList.add('hide');
  }}
  Array.prototype.forEach.call(root.querySelectorAll('.pin'),function(p){{
    var go=function(){{show(p.getAttribute('data-id'));}};
    p.addEventListener('click',go);
    p.addEventListener('keydown',function(e){{
      if(e.key==='Enter'||e.key===' '){{e.preventDefault();go();}}
    }});
  }});

  var mb=document.getElementById('hkob-map'), lb=document.getElementById('hkob-list');
  var mv=document.getElementById('hkob-mapview'), lv=document.getElementById('hkob-listview');
  function view(isMap){{
    mb.setAttribute('aria-pressed',isMap); lb.setAttribute('aria-pressed',!isMap);
    mv.classList.toggle('hide',!isMap); lv.classList.toggle('hide',isMap);
  }}
  mb.addEventListener('click',function(){{view(true);}});
  lb.addEventListener('click',function(){{view(false);}});

  var q=document.getElementById('hkob-q');
  q.addEventListener('input',function(){{
    var t=q.value.trim().toLowerCase();
    Array.prototype.forEach.call(root.querySelectorAll('.pin'),function(p){{
      if(shut(p.getAttribute('data-close'))) return;
      var d=D[p.getAttribute('data-id')]||{{}};
      var hay=((d.title||'')+' '+(d.sub||'')+' '+
        (d.items||[]).map(function(i){{return i.head+' '+i.body;}}).join(' ')).toLowerCase();
      p.style.display=(!t||hay.indexOf(t)>-1)?'':'none';
    }});
    Array.prototype.forEach.call(root.querySelectorAll('.row, .sch li'),function(el){{
      el.style.display=(!t||el.textContent.toLowerCase().indexOf(t)>-1)?'':'none';
    }});
  }});
}})();
</script>
<!--kg-card-end: html-->
"""



TEMPLATE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>What you can object to in Hackney right now</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box}}
:root{{
  --ink:#1a1a1a; --quiet:#5b5b57; --paper:#fafaf7; --chip:#eeece5;
  --land:#eceae2; --wardline:#d6d3c9; --edge:#4a4a46; --rule:#d7d5cc;
  --brand:#1a1a1a; --accent:#a8322a; --sea:#f4f3ee;
  --tip:#f7f9e9;
  color-scheme:light;
  padding-top:env(safe-area-inset-top,0px);
}}
html,body{{height:100%;margin:0;overflow-x:hidden}}
body{{font-family:Lora,Georgia,'Times New Roman',serif;color:var(--ink);
  background:var(--paper);line-height:1.5}}
.masthead{{background:var(--brand);color:#fafaf7;margin:-1.5rem -1.4rem 1.2rem;
  padding:1.3rem 1.4rem 1.1rem}}
.masthead h1{{color:#fafaf7;margin:0}}
.masthead .standfirst{{color:#bdbab2;margin:.3rem 0 0}}
.shell{{display:flex;height:100%;align-items:stretch;
  max-width:100%}}
.panel{{flex:0 0 25rem;max-width:25rem;min-width:0;overflow-y:auto;
  padding:1.5rem 1.4rem 3rem;border-right:1px solid var(--rule)}}
.stage{{flex:1 1 auto;min-width:0;position:relative;
  background:var(--sea);overflow:hidden}}
.stage svg{{position:absolute;inset:0;width:100%;height:100%;touch-action:none;
  cursor:grab}}
.stage svg.drag{{cursor:grabbing}}
h1{{font-size:1.6rem;line-height:1.15;margin:0 0 .25rem}}
.standfirst{{margin:0 0 1.1rem;color:var(--quiet);font-size:.97rem}}
.search{{display:flex;align-items:center;gap:.5rem;border:1px solid var(--rule);
  border-radius:999px;padding:.55rem 1rem;background:#fff;
  margin-bottom:.7rem}}
.search svg{{flex:0 0 auto;width:15px;height:15px}}
.search input{{border:0;outline:0;font:inherit;font-size:.97rem;width:100%;
  background:transparent;color:var(--ink)}}
.kinds{{display:flex;gap:.4rem;margin-bottom:.9rem}}
.kinds button{{font:inherit;font-size:.88rem;padding:.35rem .8rem;
  border:1px solid var(--rule);border-radius:999px;background:var(--paper);
  color:var(--ink);cursor:pointer}}
.kinds button[aria-pressed=true]{{background:var(--brand);color:var(--accent);
  border-color:var(--brand)}}
.count{{font-size:.93rem;color:var(--quiet);padding-bottom:.8rem;
  border-bottom:1px solid var(--rule);margin-bottom:1rem}}
.count b{{color:var(--ink)}}
.sel{{border-left:4px solid var(--rule);padding:.1rem 0 .1rem .8rem;
  margin:0 0 1.2rem}}
.sel .tag{{display:inline-block;font-size:.7rem;letter-spacing:.06em;
  text-transform:uppercase;background:var(--chip);border-radius:5px;
  padding:.22rem .55rem;margin-bottom:.4rem}}
.sel h2{{font-size:1.1rem;margin:0 0 .15rem}}
.sel .sub{{margin:0 0 .5rem;color:var(--quiet);font-size:.88rem}}
.sel .it{{border-top:1px solid var(--rule);padding-top:.5rem;margin-top:.5rem}}
.sel .it b{{display:block;font-size:.9rem;margin-bottom:.15rem}}
.sel .it span{{font-size:.88rem;color:var(--quiet)}}
.sel .close{{margin-top:.6rem;font-size:.9rem}}
.legend{{list-style:none;display:flex;flex-wrap:wrap;gap:.3rem 1rem;padding:0;
  margin:0 0 1.2rem;font-size:.85rem}}
.legend li{{display:flex;align-items:center;gap:.4rem}}
.sw{{display:inline-block;width:1.1em;height:.32em;border-radius:2px;
  flex:0 0 auto}}
.sw.sq{{width:.7em;height:.7em;border-radius:50%}}
h3{{font-size:1rem;margin:1.4rem 0 .4rem}}
ul.rows{{list-style:none;padding:0;margin:0}}
.row{{display:flex;flex-wrap:wrap;gap:.2rem .5rem;
  align-items:baseline;padding:.5rem 0;min-width:0;
  border-bottom:1px solid var(--rule);font-size:.9rem;
  overflow-wrap:anywhere}}
.row b{{flex:1 1 100%}}
.row .sw{{flex:0 0 auto}}
.row .sw{{margin-top:.4rem}}
.dl{{color:var(--quiet);font-size:.88rem;white-space:nowrap}}
.flag{{color:#8a5a2b;font-size:.88rem}}
.sch{{border-top:1px solid var(--rule);padding-top:.7rem;margin-top:.9rem}}
.sch h4{{font-size:1rem;margin:0 0 .2rem;display:flex;align-items:center;
  gap:.5rem}}
.sch .adv{{margin:.1rem 0 .35rem;color:var(--quiet);font-size:.85rem}}
.sch ul{{padding-left:1.05rem;margin:.3rem 0 0}}
.sch li{{font-size:.88rem;margin:.2rem 0}}
.nopc{{margin-top:1rem;font-size:.9rem;color:var(--quiet)}}
[data-shut]{{opacity:.55}}
.foot{{margin-top:1.6rem;color:var(--quiet);font-size:.8rem}}
.hide{{display:none}}
.wards{{fill:var(--land);stroke:var(--wardline);stroke-width:.8;
  stroke-linejoin:round;vector-effect:non-scaling-stroke}}
.outline{{fill:none;stroke:var(--edge);stroke-width:2;stroke-linejoin:round;
  vector-effect:non-scaling-stroke}}
.veil{{fill:var(--sea);opacity:.74;pointer-events:none}}
.rdc,.rdf{{fill:none;stroke-linecap:round;stroke-linejoin:round}}
/* casings */
.rdc1{{stroke:#d3d0c5;stroke-width:1.5}}
.rdc2{{stroke:#c7c3b6;stroke-width:2.4}}
.rdc3{{stroke:#b2ada0;stroke-width:3.4}}
.rdc4{{stroke:#97928a;stroke-width:4.4}}
/* fills */
.rdf1{{stroke:#fafaf7;stroke-width:.85}}
.rdf2{{stroke:#f7f4ec;stroke-width:1.5}}
.rdf3{{stroke:#ffffff;stroke-width:2.2}}
.rdf4{{stroke:#ffffff;stroke-width:3.0}}
.wl{{font-size:11px;fill:#6f6a52;text-anchor:middle;
  font-family:Lora,Georgia,serif;paint-order:stroke;stroke:var(--land);
  stroke-width:3px;stroke-linejoin:round;pointer-events:none}}
.ln{{fill:none;stroke-width:2.5;stroke-linecap:round;opacity:.45;
  vector-effect:non-scaling-stroke}}
.lead{{stroke:#8d8880;stroke-width:.9;vector-effect:non-scaling-stroke}}
/* Street labels: hidden until the reveal bands below switch them on. */
.rp{{fill:none;stroke:none}}
.rl{{font-family:Lora,Georgia,serif;fill:#5b5b57;font-size:9px;
  paint-order:stroke;stroke:var(--land);stroke-width:2.6px;
  stroke-linejoin:round;pointer-events:none;display:none}}
.rl3,.rl4{{fill:#3d352b}}
svg[data-k="2"] .rl3,svg[data-k="2"] .rl4,
svg[data-k="3"] .rl2,svg[data-k="3"] .rl3,svg[data-k="3"] .rl4,
svg[data-k="4"] .rl{{display:inline}}
.pin{{cursor:pointer}}
.pin circle,.pin rect{{stroke:#fafaf7;stroke-width:2;
  vector-effect:non-scaling-stroke}}
.pin text{{fill:#fafaf7;font-size:11px;font-weight:600;text-anchor:middle;
  font-family:Lora,Georgia,serif;pointer-events:none}}
.pin .gly{{overflow:visible;pointer-events:none;color:var(--paper)}}
.pin .mark{{fill:var(--paper)}}
.pin .mark [stroke]{{stroke:var(--paper)}}
.pin:hover circle,.pin:hover rect{{stroke:var(--ink)}}
.pin:focus{{outline:none}}
.pin:focus-visible circle,.pin:focus-visible rect{{
  stroke:var(--accent);stroke-width:3.5}}
.pin.on circle,.pin.on rect{{stroke:var(--ink);stroke-width:3}}
.zoom{{position:absolute;right:14px;top:14px;display:flex;flex-direction:column;
  gap:.3rem}}
.zoom button{{font:inherit;width:2.1rem;height:2.1rem;border-radius:8px;
  border:1px solid var(--rule);background:#fff;color:var(--ink);
  cursor:pointer;font-size:1rem;line-height:1}}
.mcard{{position:absolute;left:16px;bottom:16px;width:min(24rem,64%);
  background:var(--paper);border-radius:12px;padding:1rem 1.1rem;
  box-shadow:0 12px 30px rgba(26,26,26,.26),0 2px 6px rgba(26,26,26,.12);
  font-size:.9rem;max-height:62%;overflow:auto;z-index:6;
  border-left:5px solid var(--rule)}}
.mcard .tag{{display:inline-block;font-size:.68rem;letter-spacing:.07em;
  text-transform:uppercase;background:var(--chip);border-radius:5px;
  padding:.25rem .6rem;margin-bottom:.5rem}}
.mcard h2{{font-size:1.15rem;margin:0 0 .15rem}}
.mcard .sub{{margin:0 0 .6rem;color:var(--quiet);font-size:.85rem}}
.mcard .it{{border-top:1px solid var(--rule);padding-top:.5rem;margin-top:.5rem}}
.mcard .it b{{display:block;font-size:.9rem;margin-bottom:.1rem}}
.mcard .it span{{font-size:.86rem;color:var(--quiet)}}
.mcard .close{{margin-top:.7rem;font-size:.9rem;font-weight:600}}
.mcard .more{{display:inline-block;margin-top:.5rem;font-size:.86rem;
  color:var(--accent)}}
.mcard .x{{position:absolute;right:.5rem;top:.4rem;border:0;background:none;
  font:inherit;font-size:1.2rem;line-height:1;color:var(--quiet);
  cursor:pointer;padding:.2rem .4rem}}
.hint{{position:absolute;left:14px;bottom:14px;background:var(--paper);
  border-radius:999px;padding:.4rem .9rem;font-size:.82rem;color:var(--quiet);
  box-shadow:0 2px 8px rgba(0,0,0,.1)}}
@media (max-width:1100px){{
  .panel{{flex:0 0 20rem;max-width:20rem}}
}}
@media (max-width:860px){{
  html,body{{height:auto}}
  .shell{{display:block}}
  .panel{{max-width:none;flex:none;width:100%;border-right:0;
    border-bottom:1px solid var(--rule)}}
  .stage{{height:70vh}}
}}
</style>
</head>
<body>
<div class="shell">
<aside class="panel">
  <div class="masthead">
    <h1>What you can object to in Hackney right now</h1>
    <p class="standfirst">Planning applications and road changes open for
     comment or objection, with the deadline for each. Click a marker. Round
     markers are buildings; marked ones are road orders, and the symbol shows
     what each changes. A number means more than one application at that spot.</p>
  </div>

  <label class="search">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#5f5f6d"
         stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/>
      <path d="M20 20l-4-4"/></svg>
    <input id="q" type="search" placeholder="Street, postcode or reference…"
           aria-label="Filter by street, postcode or reference">
  </label>
  <div class="kinds" role="group" aria-label="Show">
    <button id="k-all" aria-pressed="true">Everything</button>
    <button id="k-plan" aria-pressed="false">Buildings</button>
    <button id="k-road" aria-pressed="false">Roads</button>
  </div>

  <p class="count"><b id="n-plan">{nplan} planning applications</b>
   <span id="n-road">and {ntraffic} road schemes</span> open now</p>

  <ul class="legend">{legend}</ul>

  <h3>Planning applications</h3>
  <ul class="rows">
{planning}
  </ul>
  <h3>Road changes</h3>
{traffic}
  {unplaced}

  <p class="foot">Planning applications advertised by Hackney Council and
   reported in the Hackney Citizen; the deadline is 21 days from the date of
   the notice, which is the only published date by which a comment must be
   made — the council’s own register shows when it intends to decide, not when
   you can object. Road changes from the council’s published traffic order
   data, retrieved {generated}. Boundaries: GLA and ONS London ward
   boundaries. Roads and postcode positions: OS Open Roads and OS Code-Point
   Open, © Crown copyright and database right, Open Government Licence v3.
   Markers sit at the postcode, not the building, and are nudged apart where
   they would overlap, with a hairline back to the true position. Deadlines
   are checked against your device’s clock.</p>
</aside>

<main class="stage">
  <svg id="map" viewBox="0 0 {vieww} {viewh}" preserveAspectRatio="xMidYMid meet"
       role="img" aria-label="Map of Hackney and its wards showing planning
       applications and road changes open for objection. Everything shown is
       also listed in the panel.">
    <g id="z">
      <path class="outline" d="{wardpath}"/>
      <path class="wards" d="{wardpath}"/>
{roads}
{roadlabels}
      <path class="veil" fill-rule="evenodd"
            d="M0,0 H{vieww} V{viewh} H0 Z {wardpath}"/>
      <path class="outline" d="{wardpath}"/>
{wardlabels}
{lines}
{pins}
    </g>
  </svg>
  <div class="zoom">
    <button id="zin" aria-label="Zoom in">+</button>
    <button id="zout" aria-label="Zoom out">−</button>
    <button id="zreset" aria-label="Reset the view">⤾</button>
  </div>
  <div class="mcard hide" id="mcard" aria-live="polite"></div>
  <div class="hint" id="hint">Scroll to zoom · drag to pan</div>
</main>
</div>
<script>
(function(){{
  var D={detail};
  var today;
  try{{today=new Intl.DateTimeFormat('en-CA',{{timeZone:'Europe/London',
    year:'numeric',month:'2-digit',day:'2-digit'}}).format(new Date());}}
  catch(e){{var n=new Date();today=n.getFullYear()+'-'+('0'+(n.getMonth()+1)).slice(-2)+'-'+('0'+n.getDate()).slice(-2);}}
  var shut=function(c){{return c && today>c;}};
  var $=function(id){{return document.getElementById(id);}};
  var pins=[].slice.call(document.querySelectorAll('.pin'));

  document.querySelectorAll('.dl[data-close]').forEach(function(el){{
    el.textContent=el.getAttribute(shut(el.getAttribute('data-close'))
      ?'data-closed':'data-open');
  }});
  document.querySelectorAll('[data-close]').forEach(function(el){{
    if(!el.classList.contains('dl') && shut(el.getAttribute('data-close')))
      el.setAttribute('data-shut','1');
  }});
  pins.forEach(function(p){{
    if(shut(p.getAttribute('data-close'))) p.style.display='none';
  }});

  function openCount(csv){{
    return csv.split(',').filter(function(c){{return c && today<=c;}}).length;
  }}
  var np=openCount('{plan_closes}'), nt=openCount('{traffic_closes}');
  $('n-plan').textContent=np+(np===1?' planning application':' planning applications');
  $('n-road').textContent='and '+nt+(nt===1?' road scheme':' road schemes');

  // ---- selection -------------------------------------------------------
  var mcard=$('mcard');
  function hideCard(){{ mcard.classList.add('hide'); }}
  function show(id,el){{
    var d=D[id]; if(!d) return;
    pins.forEach(function(p){{p.classList.remove('on');}});
    if(el) el.classList.add('on');
    var closed=shut(d.close);
    var items=d.items.map(function(it){{
      var h=it.head?'<b>'+(it.url?'<a href="'+it.url+'" rel="noopener" target="_blank">'+it.head+'</a>':it.head)+'</b>':'';
      return '<div class="it">'+h+'<span>'+it.body+
        (it.note?' <i>'+it.note+'</i>':'')+'</span></div>';
    }}).join('');
    var more=d.more?'<a class="more" href="'+d.more+'" rel="noopener" '+
      'target="_blank">'+d.moreTxt+' &rarr;</a>':'';
    mcard.innerHTML='<button class="x" aria-label="Close">&times;</button>'+
      '<span class="tag">'+
      (closed?'Closed':(d.kind==='planning'?'Planning':'Road change'))+
      '</span><h2>'+d.title+'</h2><p class="sub">'+d.sub+
      (d.approx?' · position approximate':'')+'</p>'+items+
      '<p class="close">'+(closed?'Closed ':'Comment by ')+d.closeTxt+'</p>'+
      more;
    mcard.style.borderLeftColor=d.colour;
    mcard.classList.remove('hide');
    mcard.querySelector('.x').addEventListener('click',hideCard);
  }}
  pins.forEach(function(p){{
    p.addEventListener('keydown',function(e){{
      if(e.key==='Enter'||e.key===' '){{
        e.preventDefault(); show(p.getAttribute('data-id'),p);
      }}
    }});
  }});

  // svg, g and drag are declared before first use.
  var svg=$('map'), g=$('z'), VW={vieww}, VH={viewh};
  var drag=null;

  // Clicks are detected here rather than on the markers: the pan code
  // captures the pointer, which redirects the click away from whatever was
  // under it. A pointerdown and pointerup within a few pixels is a click;
  // anything further is a drag.
  var downAt=null, downPin=null;
  svg.addEventListener('pointerdown',function(e){{
    downAt=[e.clientX,e.clientY];
    downPin=e.target.closest ? e.target.closest('.pin') : null;
  }});
  svg.addEventListener('pointerup',function(e){{
    if(!downAt) return;
    var moved=Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1]);
    downAt=null;
    if(moved>5) return;
    if(downPin) show(downPin.getAttribute('data-id'),downPin);
    else hideCard();
  }});

  // ---- zoom and pan ----------------------------------------------------
  var k=1, tx=0, ty=0;
  function apply(){{
    g.setAttribute('transform','translate('+tx+','+ty+') scale('+k+')');
    // Markers and labels keep their size, so the map gets more detailed
    // rather than merely bigger.
    var inv=1/k;
    pins.forEach(function(p){{
      p.setAttribute('transform','translate('+p.getAttribute('data-x')+','+
        p.getAttribute('data-y')+') scale('+inv+')');
    }});
    document.querySelectorAll('.wl').forEach(function(t){{
      t.style.fontSize=(11*inv)+'px';
      t.style.strokeWidth=(3*inv)+'px';
    }});
    // Street names appear in bands: main roads from 1.8x, B-roads from 2.6x,
    // everything from 3.6x. Below that the borough is too small to read them.
    var band = k>=3.6?'4':k>=2.6?'3':k>=1.8?'2':'1';
    svg.setAttribute('data-k',band);
    document.querySelectorAll('.rl').forEach(function(t){{
      t.style.fontSize=(9*inv)+'px';
      t.style.strokeWidth=(2.6*inv)+'px';
    }});
  }}
  function clamp(){{
    k=Math.max(1,Math.min(10,k));
    var maxx=VW*(k-1), maxy=VH*(k-1);
    tx=Math.max(-maxx,Math.min(0,tx)); ty=Math.max(-maxy,Math.min(0,ty));
  }}
  function zoomAt(cx,cy,factor){{
    var k0=k; k=k0*factor; clamp();
    var r=k/k0;
    tx=cx-(cx-tx)*r; ty=cy-(cy-ty)*r; clamp(); apply();
  }}
  function svgPoint(evt){{
    var r=svg.getBoundingClientRect();
    var sx=VW/r.width, sy=VH/r.height, sc=Math.max(sx,sy);
    return [(evt.clientX-r.left)*sc-(sc*r.width-VW)/2,
            (evt.clientY-r.top)*sc-(sc*r.height-VH)/2];
  }}
  svg.addEventListener('wheel',function(e){{
    e.preventDefault();
    var p=svgPoint(e);
    zoomAt(p[0],p[1],e.deltaY<0?1.18:1/1.18);
  }},{{passive:false}});
  svg.addEventListener('pointerdown',function(e){{
    drag=svgPoint(e); svg.classList.add('drag'); svg.setPointerCapture(e.pointerId);
  }});
  svg.addEventListener('pointermove',function(e){{
    if(!drag) return;
    var p=svgPoint(e);
    tx+=p[0]-drag[0]; ty+=p[1]-drag[1]; drag=p; clamp(); apply();
  }});
  ['pointerup','pointercancel','pointerleave'].forEach(function(ev){{
    svg.addEventListener(ev,function(){{drag=null;svg.classList.remove('drag');}});
  }});
  $('zin').addEventListener('click',function(){{zoomAt(VW/2,VH/2,1.4);}});
  $('zout').addEventListener('click',function(){{zoomAt(VW/2,VH/2,1/1.4);}});
  $('zreset').addEventListener('click',function(){{
    k=1;tx=0;ty=0;apply();
  }});

  // ---- filters ---------------------------------------------------------
  var kind='all';
  function matches(p){{
    if(shut(p.getAttribute('data-close'))) return false;
    if(kind!=='all' && p.classList.contains(kind==='plan'?'traffic':'planning'))
      return false;
    var t=$('q').value.trim().toLowerCase();
    if(!t) return true;
    var d=D[p.getAttribute('data-id')]||{{}};
    var hay=((d.title||'')+' '+(d.sub||'')+' '+(d.items||[]).map(function(i){{
      return i.head+' '+i.body;}}).join(' ')).toLowerCase();
    return hay.indexOf(t)>-1;
  }}
  function refilter(){{
    pins.forEach(function(p){{p.style.display=matches(p)?'':'none';}});
    var t=$('q').value.trim().toLowerCase();
    document.querySelectorAll('.row, .sch li').forEach(function(el){{
      el.style.display=(!t||el.textContent.toLowerCase().indexOf(t)>-1)?'':'none';
    }});
  }}
  $('q').addEventListener('input',refilter);
  [['k-all','all'],['k-plan','plan'],['k-road','road']].forEach(function(pair){{
    $(pair[0]).addEventListener('click',function(){{
      kind=pair[1];
      ['k-all','k-plan','k-road'].forEach(function(id){{
        $(id).setAttribute('aria-pressed', id===pair[0]);
      }});
      refilter();
    }});
  }});
  apply();
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
