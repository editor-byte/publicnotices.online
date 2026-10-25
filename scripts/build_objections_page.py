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
  hackney-landmarks.json      schools, churches, town hall and so on,
                              from make_buildings.py (optional)
  hackney-postcodes.json      postcode centroids, OS Code-Point Open
  planning-applications.csv   from parse_planning.py
  traffic_probe/traffic-geometry-*.geojson   from traffic_ingest.py

Output: objections-card.html, a self-contained Ghost HTML card, or with
--fullpage, objections.html, a standalone page.

The full page is drawn with MapLibre on a Protomaps base map: streets,
street names, parks, water and ordinary buildings all come from
hackney.pmtiles, a Hackney extract of the Protomaps OpenStreetMap build,
so hackney-roads.json and hackney-buildings.json are not used for it. Put
hackney.pmtiles on the server beside objections.html, or pass --tiles-url
with wherever it lives. Make or refresh it with the pmtiles tool:

    pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles \
        hackney.pmtiles --bbox=-0.115,51.512,-0.01,51.582 --maxzoom=15

The wards, the landmark tints, the road schemes and the markers are drawn
on top of it from this script's own data, as before. The page has to be
opened over HTTP (python3 -m http.server, or the live site), not by
double-clicking the file, because the base map is read in pieces.

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
    "Conditions and amendments": "#7d7466",    # pale stone
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

# Landmark fills: quiet tints of the land colour, so a school or a church
# reads as a different kind of building without competing with the
# markers, which keep the strong colours.
LANDMARK_FILL = {
    "Education": "#d6cba6",
    "Religious Buildings": "#cfc0b3",
    "Medical Care": "#bdd0ca",
    "Cultural Facility": "#d0bfcd",
    "Retail": "#d9c9b0",
    "Sports Or Exercise Facility": "#c3d0b2",
    "Emergency Service": "#d8b9b0",
    "Road Transport": "#c9c9c4",
    "Civic": "#bcc3d6",
}
LANDMARK_FALLBACK = "#d2ccbd"

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
    # A sheet of paper with lines: a procedural application, following a
    # permission already granted.
    "doc": '<path d="M6 2.8h8.4L19 7.4v13.8H6z"/>'
           '<rect x="8.4" y="11" width="8.2" height="1.6" fill="#fff"/>'
           '<rect x="8.4" y="14.4" width="8.2" height="1.6" fill="#fff"/>'
           '<rect x="8.4" y="17.8" width="5" height="1.6" fill="#fff"/>',
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
    "Conditions and amendments": "doc",
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


def osgb_to_wgs84(e, n):
    """British National Grid easting and northing to WGS84 longitude and
    latitude, the reverse of wgs84_to_osgb and using the same Helmert
    transformation, so everything converted either way lines up."""
    a, b = 6377563.396, 6356256.909
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2)
    N0, E0 = -100000.0, 400000.0
    e2 = 1 - (b*b)/(a*a)
    nn = (a-b)/(a+b)
    lat, M = lat0, 0.0
    while True:
        lat = (n - N0 - M)/(a*F0) + lat
        Ma = (1+nn+1.25*nn**2+1.25*nn**3)*(lat-lat0)
        Mb = (3*nn+3*nn**2+2.625*nn**3)*math.sin(lat-lat0)*math.cos(lat+lat0)
        Mc = (1.875*nn**2+1.875*nn**3)*math.sin(2*(lat-lat0))*math.cos(2*(lat+lat0))
        Md = (35/24)*nn**3*math.sin(3*(lat-lat0))*math.cos(3*(lat+lat0))
        M = b*F0*(Ma-Mb+Mc-Md)
        if abs(n - N0 - M) < 0.00001:
            break
    sl, cl, tl = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a*F0/math.sqrt(1-e2*sl*sl)
    rho = a*F0*(1-e2)/(1-e2*sl*sl)**1.5
    eta2 = nu/rho - 1
    VII = tl/(2*rho*nu)
    VIII = tl/(24*rho*nu**3)*(5+3*tl**2+eta2-9*tl**2*eta2)
    IX = tl/(720*rho*nu**5)*(61+90*tl**2+45*tl**4)
    X = 1/(cl*nu)
    XI = 1/(cl*6*nu**3)*(nu/rho+2*tl**2)
    XII = 1/(cl*120*nu**5)*(5+28*tl**2+24*tl**4)
    XIIA = 1/(cl*5040*nu**7)*(61+662*tl**2+1320*tl**4+720*tl**6)
    dE = e - E0
    lat = lat - VII*dE**2 + VIII*dE**4 - IX*dE**6
    lon = lon0 + X*dE - XI*dE**3 + XII*dE**5 - XIIA*dE**7
    # OSGB36 to WGS84: the forward transformation's parameters, negated.
    sl, cl = math.sin(lat), math.cos(lat)
    v = a/math.sqrt(1 - e2*sl*sl)
    x1, y1, z1 = v*cl*math.cos(lon), v*cl*math.sin(lon), (1-e2)*v*sl
    tx, ty, tz = -446.448, 125.157, -542.060
    s = 20.4894e-6
    rx = math.radians(-0.1502/3600); ry = math.radians(-0.2470/3600)
    rz = math.radians(-0.8421/3600)
    x2 = tx + x1*(1+s) - y1*rz + z1*ry
    y2 = ty + x1*rz + y1*(1+s) - z1*rx
    z2 = tz - x1*ry + y1*rx + z1*(1+s)
    a2, b2 = 6378137.000, 6356752.3142
    e22 = 1 - (b2*b2)/(a2*a2)
    p = math.sqrt(x2*x2 + y2*y2)
    lat = math.atan2(z2, p*(1-e22))
    for _ in range(12):
        v = a2/math.sqrt(1 - e22*math.sin(lat)**2)
        lat = math.atan2(z2 + e22*v*math.sin(lat), p)
    return math.degrees(math.atan2(y2, x2)), math.degrees(lat)


def ll_ring(ring):
    """A ring in grid metres as [lon, lat] pairs, rounded to about 10 cm."""
    out = []
    for e, n in ring:
        lon, lat = osgb_to_wgs84(e, n)
        out.append([round(lon, 6), round(lat, 6)])
    return out


def signed_area(ring):
    return sum(ring[i][0]*ring[i+1][1] - ring[i+1][0]*ring[i][1]
               for i in range(len(ring) - 1)) / 2


def borough_outline(wards):
    """
    Hackney's outer edge, for the dark line round the borough and the hole
    in the veil. First by dropping every edge two wards share, which is
    exact when neighbours share their boundary points; otherwise by tracing
    round the wards painted onto a fine grid. Returns (rings, how), or
    (None, reason) if neither works.
    """
    key = lambda p: (round(p[0], 1), round(p[1], 1))
    edges = collections.Counter()
    total = 0.0
    for w in wards:
        for ring in w["rings"]:
            total += ring_area(ring)
            for i in range(len(ring) - 1):
                a, b = key(ring[i]), key(ring[i+1])
                if a != b:
                    edges[frozenset((a, b))] += 1
    # Hackney is one piece, so a true outline is one ring that encloses
    # all the wards' area. Wards whose borders do not match come back as
    # their own separate rings instead, and fail this.
    rings = _chain(tuple(e) for e, c in edges.items() if c % 2)
    if rings:
        big = max(rings, key=ring_area)
        if abs(ring_area(big) - total) <= 0.01*total:
            return [big], "traced from the wards"
    rings = _raster_outline(wards)
    if rings and abs(ring_area(rings[0]) - total) <= 0.03*total:
        return rings, "traced round the wards on a 4-metre grid"
    return None, ("could not be traced" if not rings else
                  f"traced, but its area is {ring_area(rings[0])/total:.0%} "
                  f"of the wards'")


def _chain(edges):
    """Closed rings from a set of undirected edges, every point of which
    joins an even number of them."""
    adj = collections.defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    if not adj or any(len(v) % 2 for v in adj.values()):
        return None
    used, rings = set(), []
    for start in list(adj):
        while any(frozenset((start, q)) not in used for q in adj[start]):
            ring, cur = [start], start
            while True:
                nxt = next((q for q in adj[cur]
                            if frozenset((cur, q)) not in used), None)
                if nxt is None:
                    return None
                used.add(frozenset((cur, nxt)))
                ring.append(nxt)
                cur = nxt
                if cur == start:
                    break
            rings.append(ring)
    return rings


def _simplify(pts, tol):
    """Douglas-Peucker on an open run of points."""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1e-9
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy*(pts[i][0]-x1) - dx*(pts[i][1]-y1)) / L
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return _simplify(pts[:idx+1], tol)[:-1] + _simplify(pts[idx:], tol)


def _raster_outline(wards, cell=4.0, close=2):
    """
    Hackney's outer edge when the wards' shared borders do not match point
    for point: paint every ward onto a grid of 4-metre cells, close any
    hairline gaps between neighbours, then trace round what is painted.
    Accurate to a few metres, which at the closest zoom is a pixel or two.
    """
    xs = [p[0] for w in wards for r in w["rings"] for p in r]
    ys = [p[1] for w in wards for r in w["rings"] for p in r]
    pad = close + 2
    x0, y0 = min(xs) - pad*cell, min(ys) - pad*cell
    nx = int((max(xs) - x0)/cell) + pad + 1
    ny = int((max(ys) - y0)/cell) + pad + 1
    rows = [0]*ny                      # one int per row, a bit per cell
    for w in wards:
        for ring in w["rings"]:
            ring_rows = [0]*ny
            ylo = max(0, int((min(p[1] for p in ring) - y0)/cell))
            yhi = min(ny - 1, int((max(p[1] for p in ring) - y0)/cell) + 1)
            edges = [(ring[i], ring[i+1]) for i in range(len(ring) - 1)]
            for j in range(ylo, yhi + 1):
                yc = y0 + (j + .5)*cell
                hits = sorted(a[0] + (yc - a[1])*(b[0] - a[0])/(b[1] - a[1])
                              for a, b in edges if (a[1] > yc) != (b[1] > yc))
                for k in range(0, len(hits) - 1, 2):
                    c0 = max(0, int((hits[k] - x0)/cell + .5))
                    c1 = min(nx, int((hits[k+1] - x0)/cell + .5))
                    if c1 > c0:
                        ring_rows[j] ^= ((1 << (c1 - c0)) - 1) << c0
            for j in range(ny):
                rows[j] |= ring_rows[j]
    full = (1 << nx) - 1
    def grow(r):
        r = [(v | v << 1 | v >> 1) & full for v in r]
        return [r[j] | (r[j-1] if j else 0) | (r[j+1] if j < ny-1 else 0)
                for j in range(ny)]
    def shrink(r):
        r = [v & (v << 1) & (v >> 1) for v in r]
        return [r[j] & (r[j-1] if j else 0) & (r[j+1] if j < ny-1 else 0)
                for j in range(ny)]
    for _ in range(close):
        rows = grow(rows)
    for _ in range(close):
        rows = shrink(rows)
    # Cell edges with painted on one side and not the other.
    edges = []
    for j in range(ny):
        v = rows[j] ^ (rows[j] << 1)           # vertical edges in this row
        while v:
            i = (v & -v).bit_length() - 1
            edges.append(((i, j), (i, j + 1)))
            v &= v - 1
        h = rows[j] ^ (rows[j+1] if j < ny - 1 else 0)   # above this row
        while h:
            i = (h & -h).bit_length() - 1
            edges.append(((i, j + 1), (i + 1, j + 1)))
            h &= h - 1
        if j == 0:
            h = rows[0]
            while h:
                i = (h & -h).bit_length() - 1
                edges.append(((i, 0), (i + 1, 0)))
                h &= h - 1
    rings = _chain(edges)
    if not rings:
        return None
    ring = max(rings, key=lambda r: ring_area(r))     # Hackney is one piece
    # A closed ring starts and ends at the same point, so simplify it as
    # two runs, split at the point farthest from the start.
    far = max(range(len(ring)), key=lambda i: math.dist(ring[0], ring[i]))
    ring = _simplify(ring[:far+1], 0.75)[:-1] + _simplify(ring[far:], 0.75)
    return [[[x0 + i*cell, y0 + j*cell] for i, j in ring]]


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
    A label longer than the road it names reads as detached from it, so
    require the road to be comfortably longer than the label will be at the
    zoom where it first appears.
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
    ap.add_argument("--landmarks", default="hackney-landmarks.json")
    ap.add_argument("--buildings-url", default="hackney-buildings.json",
                    help="where readers' browsers fetch the ordinary "
                         "buildings from, relative to the page or absolute")
    ap.add_argument("--tiles-url", default="hackney.pmtiles",
                    help="where readers' browsers fetch the Protomaps base "
                         "map from, relative to the page or absolute "
                         "(full page only)")
    ap.add_argument("--postcodes", default="hackney-postcodes.json")
    ap.add_argument("--planning", default="planning-applications.csv")
    ap.add_argument("--traffic", default=None)
    ap.add_argument("--fullpage", action="store_true",
                    help="standalone zoomable page instead of a Ghost card")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    today = dt.date.today()
    iso_today = today.isoformat()

    # The council publishes its weekly lists about a week in arrears, so
    # applications validated since then are in its register but in no sheet.
    # Saying so is the honest alternative to implying the page is current.
    data_to = ""
    if pathlib.Path("data-current-to.txt").exists():
        raw = pathlib.Path("data-current-to.txt").read_text().strip()
        try:
            data_to = dt.date.fromisoformat(raw).strftime("%-d %B %Y")
        except ValueError:
            data_to = ""

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

    ward_centre = {}
    for w in wards:
        big = max(w["rings"], key=ring_area)
        ward_centre[w["name"].lower()] = ring_centroid(big)

    ward_d = []
    for w in wards:
        for ring in w["rings"]:
            pts = [proj(x, y) for x, y in ring]
            ward_d.append("M" + " L".join(f"{x},{y}" for x, y in pts) + " Z")

    # The same wards in longitude and latitude, for the full page's map.
    ward_lines = [ll_ring(ring) for w in wards for ring in w["rings"]]
    lons = [p[0] for r in ward_lines for p in r]
    lats = [p[1] for r in ward_lines for p in r]
    bounds = [[min(lons), min(lats)], [max(lons), max(lats)]]
    outline, how = borough_outline(wards)
    holes = [ll_ring(r) for r in outline] if outline else ward_lines
    # The veil covers everything outside the borough: a large box with
    # Hackney cut out of it. A hole has to wind the other way from the box,
    # or the map fills it in.
    box = [[-1.5, 50.9], [1.4, 50.9], [1.4, 52.2], [-1.5, 52.2], [-1.5, 50.9]]
    veil_rings = [box] + [h if signed_area(h) < 0 else h[::-1] for h in holes]
    print("borough outline: " + (how if outline else
          how + ", using each ward instead"))

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

    # Street labels, placed at the midpoint of each road and revealed as
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
        # Placed once at the midpoint and rotated to the road's bearing,
        # rather than threaded along the geometry: a path with a bend in it
        # drops and spaces letters, which is what turned Amhurst Road into
        # "Amhust Road".
        mid = len(pts) // 2
        mx, my = pts[mid]
        ax, ay = pts[0]
        bx, by = pts[-1]
        ang = math.degrees(math.atan2(by - ay, bx - ax))
        if ang > 90:
            ang -= 180
        elif ang < -90:
            ang += 180
        road_labels.append(
            f'<text class="rl rl{w}" x="0" y="-3" text-anchor="middle" '
            f'transform="translate({round(mx,1)},{round(my,1)}) '
            f'rotate({round(ang,1)})">{html.escape(name.title())}</text>')
    road_label_svg = "".join(road_labels)
    if roads:
        print(f"{len(road_labels)} street labels from {len(named)} "
              f"named segments ({len(roads)} segments total)")

    # --- buildings ---
    # Footprints are written in whole metres from a corner of the map and
    # put in place by a single transform, so the reader's browser can draw
    # the fetched buildings file exactly as it is, without knowing anything
    # about the projection.
    bx0, by0 = math.floor(minx), math.floor(miny)
    bld_tf = (f"matrix({s:.7f} 0 0 {-s:.7f} {ox + (bx0-minx)*s:.3f} "
              f"{H - oy - (by0-miny)*s:.3f})")

    def ring_d(ring):
        return "M" + " ".join(f"{round(x-bx0)} {round(y-by0)}"
                              for x, y in ring[:-1]) + "Z"

    landmark_svg = landmark_label_svg = ""
    landmark_feats = []
    if pathlib.Path(args.landmarks).exists():
        lmdata = json.load(open(args.landmarks))
        lrings = lmdata["buildings"]
        bytheme = collections.defaultdict(list)
        biggest = {}
        for idx, theme, _cls, name in lmdata.get("landmarks", []):
            ring = lrings[idx]
            bytheme[theme or ""].append(ring_d(ring))
            landmark_feats.append({
                "type": "Feature",
                "properties": {"f": LANDMARK_FILL.get(theme or "", LANDMARK_FALLBACK),
                               "t": theme or ""},
                "geometry": {"type": "Polygon", "coordinates": [ll_ring(ring)]}})
            if name:
                a = ring_area(ring)
                if name not in biggest or a > biggest[name][0]:
                    biggest[name] = (a, ring)
        landmark_svg = "".join(
            f'<path class="lm" fill="{LANDMARK_FILL.get(t, LANDMARK_FALLBACK)}" '
            f'd="{"".join(ds)}"><title>{html.escape(t)}</title></path>'
            for t, ds in sorted(bytheme.items()))
        # One label per name, on its largest footprint, skipping any that
        # would land on top of one already placed.
        placed, lm_labels = [], []
        for name, (_a, ring) in sorted(biggest.items(), key=lambda t: -t[1][0]):
            cx, cy = proj(*ring_centroid(ring))
            if any(math.hypot(cx-px, cy-py) < 9 for px, py in placed):
                continue
            placed.append((cx, cy))
            lm_labels.append(
                f'<text class="ll" x="0" y="0" '
                f'transform="translate({cx},{cy})">{html.escape(name)}</text>')
        landmark_label_svg = "".join(lm_labels)
        print(f"{sum(len(v) for v in bytheme.values())} landmark footprints, "
              f"{len(lm_labels)} labelled")

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
            else:
                # No postcode in the council's own list. The ward is the
                # only location it gives, so use that and say so.
                wn = re.sub(r"\s+ward$", "", (r.get("ward") or "").strip(),
                            flags=re.I).lower()
                if wn in ward_centre:
                    at, how = ward_centre[wn], "ward"
        if at is None:
            unplaced.append(r)
            continue
        key = (round(at[0], 1), round(at[1], 1))
        bypc.setdefault(key, {"at": at, "how": how, "rows": []})["rows"].append(r)

    for key, grp in bypc.items():
        rows = grp["rows"]
        # A row with no deadline has not closed — the council has simply
        # not published one. Hiding it would leave a reader thinking there
        # is nothing to comment on.
        live = [r for r in rows
                if not r["comment_deadline"]
                or r["comment_deadline"] >= iso_today]
        if not live:
            continue                      # closed: list only
        pid += 1
        px, py = proj(*grp["at"])
        plon, plat = osgb_to_wgs84(*grp["at"])
        cats = [r["category"] for r in live]
        top = collections.Counter(cats).most_common(1)[0][0]
        colour = CAT_COLOUR.get(top, CAT_FALLBACK)
        dls = [r["comment_deadline"] for r in live if r["comment_deadline"]]
        deadline = min(dls) if dls else ""
        pins.append({"id": pid, "x": px, "y": py, "tx": px, "ty": py,
                     "lon": round(plon, 6), "lat": round(plat, 6),
                     "n": len(live), "c": colour, "kind": "planning",
                     "close": deadline, "glyph": CAT_GLYPH.get(top, "house")})
        detail[pid] = {
            "kind": "planning",
            "title": live[0]["postcode"] or live[0]["address"][:40],
            "sub": f"{len(live)} planning application"
                   f"{'s' if len(live) != 1 else ''}",
            "approx": grp["how"] != "exact",
            "approxTxt": {"sector": "position approximate",
                          "ward": "ward only — the council's list gives no "
                                  "postcode"}.get(grp["how"], ""),
            "close": deadline,
            "closeTxt": (fmt(deadline) if deadline else
                         "The council has not published a deadline"),
            "noDeadline": not deadline, "colour": colour,
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
            f'<b>{html.escape(r["address"])}</b>'
            f'<span class="meta">{link} · {html.escape(r["category"])}'
            f'{" · " + html.escape(r["designation"]) if r["designation"] else ""}'
            f'</span>'
            + (f'<span class="dl" data-close="{r["comment_deadline"]}" '
               f'data-open="Comment by {fmt(r["comment_deadline"])}" '
               f'data-closed="Closed {fmt(r["comment_deadline"])}">'
               f'Comment by {fmt(r["comment_deadline"])}</span></li>'
               if r["comment_deadline"] else
               '<span class="dl nodl">No deadline published</span></li>'))

    # ---------------- traffic: pin per street, line per scheme ----------
    schemes = collections.OrderedDict()
    for f in tfeats:
        schemes.setdefault(f["properties"].get("order_ref", "?"), []).append(f)

    lines = []
    line_feats = []
    for ref, fs in schemes.items():
        close = fs[0]["properties"].get("date_close", "")
        advert = fs[0]["properties"].get("date_advert", "")
        seg, seg_ll, bystreet = [], [], collections.OrderedDict()
        for f in fs:
            p = f["properties"]
            st = (p.get("street_name") or "").title()
            g = f["geometry"]
            parts = ([g["coordinates"]] if g["type"] == "LineString"
                     else g["coordinates"])
            allp, allll = [], []
            for part in parts:
                if len(part) >= 2:
                    allll += part
                    seg_ll.append([[round(x, 6), round(y, 6)] for x, y in part])
                pts = [proj(*wgs84_to_osgb(lat, lon)) for lon, lat in part]
                if len(pts) >= 2:
                    allp += pts
                    seg.append("M" + " L".join(f"{x},{y}" for x, y in
                                               lengthen(pts, MIN_DRAW)))
            rec = bystreet.setdefault(st, {"what": [], "pts": [], "ll": []})
            d = describe_traffic(p)
            if d and d not in rec["what"]:
                rec["what"].append(d)
            rec["pts"] += allp
            rec["ll"] += allll

        if close >= iso_today:
            lines.append(f'<path class="ln" stroke="{TRAFFIC_COLOUR["scheme"]}" '
                         f'd="{" ".join(seg)}"/>')
            if seg_ll:
                line_feats.append({"type": "Feature", "properties": {},
                                   "geometry": {"type": "MultiLineString",
                                                "coordinates": seg_ll}})
            for st, rec in bystreet.items():
                if not rec["pts"]:
                    continue
                pid += 1
                cx = sum(p[0] for p in rec["pts"])/len(rec["pts"])
                cy = sum(p[1] for p in rec["pts"])/len(rec["pts"])
                gl = glyph_for(rec["what"])
                llon = sum(p[0] for p in rec["ll"])/len(rec["ll"])
                llat = sum(p[1] for p in rec["ll"])/len(rec["ll"])
                pins.append({"id": pid, "x": cx, "y": cy,
                             "tx": round(cx, 1), "ty": round(cy, 1),
                             "lon": round(llon, 6), "lat": round(llat, 6),
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

    shown_first = [p for p in pins if p["close"]]
    declutter(shown_first)
    # The rest keep their true positions; they overlap each other when
    # revealed, which is honest, and they no longer distort the main view.

    obstacles = [(p["x"], p["y"]) for p in shown_first]
    labels = []
    ward_label_ll = []
    for w in wards:
        big = max(w["rings"], key=ring_area)
        ring = [proj(x, y) for x, y in big]
        pt, score = clearest_point(ring, obstacles)
        if pt is None or score < 0.55:
            pt = proj(*ring_centroid(big))
        px, py = round(pt[0], 1), round(pt[1], 1)
        name = w["name"].replace("&", "&amp;")
        words = name.split()
        # Back from the drawing to the grid, then to longitude and latitude.
        llon, llat = osgb_to_wgs84(minx + (pt[0] - ox)/s, miny + (H - oy - pt[1])/s)
        ward_label_ll.append({
            "lon": round(llon, 6), "lat": round(llat, 6),
            "html": (" ".join(words[:len(words)//2 + len(words) % 2]) + "<br>" +
                     " ".join(words[len(words)//2 + len(words) % 2:])
                     if len(name) > 13 and len(words) > 1 else name)})
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
        p["svg"] = (f'<svg viewBox="-13 -13 26 26" width="26" height="26" '
                    f'aria-hidden="true">{shape}{count}</svg>')
        pin_svg.append(
            f'<g class="pin {p["kind"]}{"" if p["close"] else " nodl"}" '
            f'data-id="{p["id"]}" '
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

    tmpl = TEMPLATE_PAGE if args.fullpage else card_from_page(TEMPLATE_PAGE)
    outname = args.out or ("objections.html" if args.fullpage
                           else "objections-card.html")
    card = tmpl.format(
        vieww=W, viewh=H, wardpath=" ".join(ward_d), roads=road_svg,
        roadlabels=road_label_svg,
        landmarks=landmark_svg, landmarklabels=landmark_label_svg,
        bldtransform=bld_tf, bldurl=json.dumps(args.buildings_url),
        bx0=bx0, by0=by0,
        wardlabels="\n".join(labels), lines="\n".join(lines),
        pins="\n".join(pin_svg), legend=legend,
        planning="\n".join(plan_sections),
        traffic="\n".join(traffic_sections),
        unplaced=unplaced_html,
        detail=json.dumps(detail),
        tilesurl=json.dumps(args.tiles_url),
        mapdata=json.dumps({
            "bounds": bounds,
            "pins": [{"id": p["id"], "lon": p["lon"], "lat": p["lat"],
                      "kind": p["kind"], "close": p["close"], "svg": p["svg"]}
                     for p in pins],
            "wardLabels": ward_label_ll,
            "wards": {"type": "Feature", "properties": {},
                      "geometry": {"type": "MultiLineString",
                                   "coordinates": ward_lines}},
            "outline": ({"type": "Feature", "properties": {},
                         "geometry": {"type": "MultiLineString",
                                      "coordinates": [ll_ring(r) for r in outline]}}
                        if outline else None),
            "veil": {"type": "Feature", "properties": {},
                     "geometry": {"type": "Polygon", "coordinates": veil_rings}},
            "lines": {"type": "FeatureCollection", "features": line_feats},
            "landmarks": {"type": "FeatureCollection", "features": landmark_feats},
        }, separators=(",", ":")),
        nplan=nplan, ntraffic=ntraffic,
        plan_closes=plan_closes, traffic_closes=traffic_closes,
        n_nodeadline=sum(1 for r in plan if not r['comment_deadline']),
        currency=(
            f"Planning data covers everything Hackney had published by "
            f"<b>{data_to}</b>, the end of its most recent weekly list. "
            f"Applications validated since then are on the council's "
            f"register but not yet in any list it publishes, so they are "
            f"not here. Road orders are current to today."
            if data_to else
            "Road orders are current to today."),
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


# ---------------------------------------------------------------- card

# The Ghost card is the same map as the standalone page: same markup, same
# JavaScript, same data. Rather than keep a second copy that would drift,
# it is derived from the page template here, with class and id names
# prefixed so nothing collides with the theme.

CARD_CLASSES = ["shell","panel","stage","masthead","standfirst","search",
    "kinds","nodl-toggle","count","legend","rows","row","meta","dl","nodl",
    "flag","sch","adv","nopc","nopc-list","currency","foot","hide","mcard",
    "tag","sub","it","close","more","x","zoom","hint","sw"]
CARD_IDS = ["map","q","k-all","k-plan","k-road","k-nodl","n-nodl","n-plan",
    "n-road","mcard","zin","zout","zreset","hint"]
PFX = "hcn-"


def card_from_page(page):
    """Turn the standalone page template into a Ghost HTML card."""
    style = page[page.find("<style>") + 7:page.find("</style>")]
    body = page[page.find("<body>") + 6:page.find("<script>")]
    # rfind: the head CDN scripts close before the inline one.
    script = page[page.find("<script>"):page.rfind("</script>") + 9]

    def prefix(text, in_css):
        for c in sorted(CARD_CLASSES, key=len, reverse=True):
            if in_css:
                text = re.sub(r"\." + re.escape(c) + r"(?![\w-])",
                              "." + PFX + c, text)
            else:
                text = text.replace('class="' + c + '"', 'class="' + PFX + c + '"')
                text = text.replace('class="' + c + ' ', 'class="' + PFX + c + ' ')
                text = text.replace(" " + c + '"', " " + PFX + c + '"')
        for i in sorted(CARD_IDS, key=len, reverse=True):
            text = text.replace('id="' + i + '"', 'id="' + PFX + i + '"')
            text = text.replace("'" + i + "'", "'" + PFX + i + "'")
        return text

    style = prefix(style, True)
    for c in ("row","meta","dl","nodl","sw","sq","flag","sch","adv","nopc",
              "nopc-list","pin","planning","traffic","rows","legend","hide"):
        style = re.sub(r"\." + PFX + re.escape(c) + r"(?![\w-])",
                       "." + PFX + c + ",." + PFX + "shell ." + c, style)
    body = prefix(body, False)
    script = prefix(script, False)

    # The page sizes itself to the window; a card must not. These rules
    # are doubled ({{ }}) because the template is still a format string.
    style = re.sub(r"html,body\{\{[^}]*\}\}", "", style)
    style = re.sub(r"\nbody\{\{[^}]*\}\}", "", style)
    style = style.replace(":root{", "." + PFX + "shell{")
    style = re.sub(r"\." + PFX + r"shell\{display:flex;height:100%;[^}]*\}",
                   "." + PFX + "shell{display:flex;flex-direction:column;"
                   "max-width:100%;font-family:Lora,Georgia,serif;"
                   "color:var(--ink);background:var(--paper);"
                   "border:1px solid var(--rule);margin:2.5rem 0 1rem}", style)

    head = page[page.find('<link rel="preconnect"'):page.find("<style>")]
    return ("<!--kg-card-begin: html-->\n" + head
            + "<style>\n"
            # the card is still a format template, so its own braces
            # have to be doubled like the rest of it
            + CARD_SHELL_CSS.replace("{", "{{").replace("}", "}}")
            + style + "\n</style>\n"
            + '<div class="kg-width-full ' + PFX + 'shell">'
            + body.replace('<div class="' + PFX + 'shell">', "", 1)
            + script + "\n<!--kg-card-end: html-->\n")


CARD_SHELL_CSS = """
/* A box in an article, not a window: the map takes a fixed height at the
   top and the panel runs beneath, so a reader meets the map first. */
.hcn-shell .hcn-stage{height:min(70vh,34rem);min-height:22rem;flex:none;
  position:relative;border-bottom:1px solid var(--rule)}
.hcn-shell .hcn-panel{max-width:none;flex:none;width:100%;border-right:0;
  padding:1.25rem 1.4rem 2rem;max-height:none;overflow:visible}
.hcn-shell .hcn-masthead{margin:-1.25rem -1.4rem 1rem}
.hcn-shell .hcn-mcard{position:static;width:auto;margin:.9rem 1.4rem 0;
  max-height:none;box-shadow:none;border:1px solid var(--rule)}
.hcn-shell .hcn-hint{display:none}
.hcn-shell h1{font-size:1.3rem}
@media (min-width:62rem){
  .hcn-shell{flex-direction:row;align-items:stretch;height:min(78vh,44rem)}
  .hcn-shell .hcn-panel{flex:0 0 22rem;max-width:22rem;overflow-y:auto;
    border-right:1px solid var(--rule)}
  .hcn-shell .hcn-stage{flex:1 1 auto;height:auto;border-bottom:0}
  .hcn-shell .hcn-mcard{position:absolute;left:16px;bottom:16px;
    width:min(24rem,64%);margin:0;box-shadow:0 12px 30px rgba(26,26,26,.26)}
}
"""


TEMPLATE_OLD = """<!--kg-card-begin: html-->
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
.hkob .count b{{color:var(--ink);font-weight:400}}
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
.hkob /* Street labels: hidden until the reveal bands below switch them on. */
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
<h2>Planning and traffic changes — what’s happening near you and what you can do about it</h2>
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

<p class="foot">Planning applications from Hackney Council’s
 weekly consultation lists. Those lists carry no date by which a comment
 must be made; where one is shown here it comes from the printed statutory
 notice, 21 days from the date of that notice. The council’s own register
 shows when it intends to decide, not when you can object. Road changes
 from the council’s published traffic order data, retrieved {generated}.
 Boundaries: GLA and ONS London ward boundaries. Roads and postcode
 positions: OS Open Roads and OS Code-Point Open, &copy; Crown copyright
 and database right, Open Government Licence v3. Markers sit at the
 postcode, not the building, and are nudged apart where they would
 overlap; a few sit at the centre of their ward because the council’s list
 gives no postcode for them. Deadlines are checked against your
 device’s clock.</p>
</div>
<script>
(function(){{
  var root=document.getElementById('hkob'); if(!root) return;
  var D={detail};
  var today;
  try{{today=new Intl.DateTimeFormat('en-CA',{{timeZone:'Europe/London',
    year:'numeric',month:'2-digit',day:'2-digit'}}).format(new Date());}}
  catch(e){{var n=new Date();today=n.getFullYear()+'-'+('0'+(n.getMonth()+1)).slice(-2)+'-'+('0'+n.getDate()).slice(-2);}}
  // No deadline means the council has not published one, not
  // that it has passed.
  var shut=function(c){{return !!c && today>c;}};

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
  var nd={n_nodeadline};
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
      (d.approxTxt?' · '+d.approxTxt:'')+'</p>'+items+
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
<title>Planning and traffic changes in Hackney — what’s happening near you and what you can do about it</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.css">
<script src="https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.js"></script>
<script src="https://cdn.jsdelivr.net/npm/pmtiles@4.5.0/dist/pmtiles.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@protomaps/basemaps@5.7.2/dist/basemaps.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box}}
:root{{
  --ink:#1a1a1a; --quiet:#5b5b57; --paper:#fafaf7; --chip:#eeece5;
  --land:#eceae2; --wardline:#d6d3c9; --edge:#4a4a46; --rule:#d7d5cc;
  --brand:#4a4a46; --accent:#4a4a46; --sea:#f4f3ee;
  --tip:#f7f9e9;
  color-scheme:light;
  padding-top:env(safe-area-inset-top,0px);
}}
html,body{{height:100%;margin:0;overflow-x:hidden}}
body{{font-family:Lora,Georgia,'Times New Roman',serif;color:var(--ink);
  background:var(--paper);line-height:1.5}}
.masthead{{background:var(--brand);color:#fafaf7;margin:-1.5rem -1.4rem 1rem;
  padding:1.15rem 1.4rem 1.05rem}}
.masthead h1{{color:#fafaf7;margin:0;font-weight:400}}
.shell{{display:flex;height:100%;align-items:stretch;
  max-width:100%}}
.panel{{flex:0 0 25rem;max-width:25rem;min-width:0;overflow-y:auto;
  padding:1.5rem 1.4rem 3rem;border-right:1px solid var(--rule)}}
.stage{{flex:1 1 auto;min-width:0;position:relative;
  background:var(--sea);overflow:hidden}}
#map{{position:absolute;inset:0}}
h1{{font-size:1.38rem;line-height:1.2;margin:0 0 .75rem;
  text-wrap:balance}}
.standfirst{{margin:0 0 1.1rem;color:var(--quiet);font-size:.97rem}}
a{{color:var(--brand);text-decoration:underline;
  text-underline-offset:2px;text-decoration-thickness:1px}}
a:hover,a:focus{{color:var(--ink);text-decoration-thickness:2px}}
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
.kinds button[aria-pressed=true]{{background:var(--brand);color:#fafaf7;
  border-color:var(--brand)}}
.nodl-toggle{{display:flex;gap:.5rem;align-items:flex-start;
  margin:0 0 .9rem;font-size:.85rem;color:var(--quiet);cursor:pointer;
  line-height:1.35}}
.nodl-toggle input{{margin-top:.22rem;flex:0 0 auto}}
.nodl-toggle b{{color:var(--ink)}}
.count{{font-size:.93rem;color:var(--quiet);padding-bottom:.8rem;
  border-bottom:1px solid var(--rule);margin-bottom:1rem}}
.count b{{color:var(--ink);font-weight:400}}
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
.row{{display:grid;grid-template-columns:1.3em 1fr;gap:.1rem .5rem;
  padding:.65rem 0;min-width:0;border-bottom:1px solid var(--rule);
  font-size:.9rem;overflow-wrap:anywhere}}
.row .sw{{grid-row:1;margin-top:.45em}}
.row b{{grid-column:2}}
.row .meta,.row .dl{{grid-column:2;color:var(--quiet);font-size:.84rem}}
.row .dl{{white-space:normal}}
.row .sw{{margin-top:.4rem}}
.dl{{color:var(--quiet);font-size:.88rem;white-space:nowrap}}
.flag{{color:#8a5a2b;font-size:.88rem}}
.nodl{{font-style:italic}}
.sch{{border-top:1px solid var(--rule);padding-top:.7rem;margin-top:.9rem}}
.sch h4{{font-size:1rem;margin:0 0 .2rem;display:flex;align-items:center;
  gap:.5rem}}
.sch .adv{{margin:.1rem 0 .35rem;color:var(--quiet);font-size:.85rem}}
.sch ul{{padding-left:1.05rem;margin:.3rem 0 0}}
.sch li{{font-size:.88rem;margin:.2rem 0}}
.nopc{{margin-top:1rem;font-size:.9rem;color:var(--quiet)}}
[data-shut]{{opacity:.55}}
.currency{{margin:1.2rem 0 0;font-size:.84rem;color:var(--quiet);
  border-top:1px solid var(--rule);padding-top:.8rem}}
.currency b{{color:var(--ink)}}
.foot{{margin-top:1rem;color:var(--quiet);font-size:.8rem}}
.hide{{display:none}}
/* Ward names: drawn over the map in the page's own typeface, and taken
   away once the reader is close enough to read street names instead. */
.wl{{font-size:12px;color:#6f6a52;text-align:center;line-height:1.15;
  font-family:Lora,Georgia,serif;pointer-events:none;white-space:nowrap;
  text-shadow:0 0 3px var(--land),0 0 3px var(--land),0 0 2px var(--land)}}
.stage[data-near] .wl{{display:none}}
.pin{{cursor:pointer;width:26px;height:26px}}
.pin svg{{display:block;overflow:visible}}
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
  stroke:var(--ink);stroke-width:3.5}}
.pin.on circle,.pin.on rect{{stroke:var(--ink);stroke-width:3}}
.zoom{{position:absolute;right:14px;top:14px;display:flex;flex-direction:column;
  gap:.3rem;z-index:5}}
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
.mcard .it b a{{color:var(--ink)}}
.mcard .it span{{font-size:.86rem;color:var(--quiet)}}
.mcard .close{{margin-top:.7rem;font-size:.9rem;font-weight:600}}
.mcard .more{{display:inline-block;margin-top:.5rem;font-size:.86rem;
  color:var(--ink);text-decoration:underline;
  text-underline-offset:2px}}
.mcard .x{{position:absolute;right:.5rem;top:.4rem;border:0;background:none;
  font:inherit;font-size:1.2rem;line-height:1;color:var(--quiet);
  cursor:pointer;padding:.2rem .4rem}}
.hint{{position:absolute;left:14px;bottom:14px;z-index:4;background:var(--paper);
  border-radius:999px;padding:.4rem .9rem;font-size:.82rem;color:var(--quiet);
  box-shadow:0 2px 8px rgba(0,0,0,.1)}}
.maplibregl-ctrl-attrib{{font-family:Lora,Georgia,serif;font-size:.72rem}}
@media (max-width:1100px){{
  .panel{{flex:0 0 20rem;max-width:20rem}}
}}
@media (max-width:860px){{
  html,body{{height:auto}}
  /* On a phone the map comes first: a reader wants to find their street,
     not read a list. The panel follows it. */
  .shell{{display:flex;flex-direction:column}}
  .stage{{order:-1;height:62vh;min-height:20rem;
    border-bottom:1px solid var(--rule)}}
  .panel{{max-width:none;flex:none;width:100%;border-right:0;
    padding-top:1.1rem}}
  /* The masthead is full-bleed in the panel, so it needs no negative top
     margin once the map sits above it. */
  .masthead{{margin:-1.1rem -1.4rem 1.2rem}}
  /* Over the bottom of the map, where a reader can see it without
     scrolling away from the marker they tapped. */
  .mcard{{left:.6rem;right:.6rem;bottom:.6rem;width:auto;max-height:70%;
    box-shadow:0 6px 18px rgba(26,26,26,.22)}}
  .hint{{display:none}}
}}
</style>
</head>
<body>
<div class="shell">
<aside class="panel">
  <div class="masthead">
    <h1>Planning and traffic changes — what’s happening near you and what
     you can do about it</h1>
  </div>
  <p class="standfirst">Everything in Hackney still open for comment, with
   the deadline for each.</p>

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
  <label class="nodl-toggle">
    <input type="checkbox" id="k-nodl">
    <span>Also show the <b id="n-nodl"></b> applications the council has
     published no deadline for</span>
  </label>

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

  <p class="currency">{currency}</p>

  <p class="foot">Planning applications from Hackney Council’s
   weekly consultation lists. Those lists carry no date by which a comment
   must be made; where one is shown here it comes from the printed statutory
   notice, 21 days from the date of that notice. The council’s own register
   shows when it intends to decide, not when you can object. Road changes
   from the council’s published traffic order data, retrieved {generated}.
   Boundaries: GLA and ONS London ward boundaries. Base map: Protomaps,
   from &copy; OpenStreetMap contributors. Landmark buildings and postcode
   positions: OS OpenMap Local and OS Code-Point Open, &copy; Crown
   copyright and database right, Open Government Licence v3. Markers sit at
   the postcode, not the building; a few sit at the centre of their ward
   because the council’s list gives no postcode for them. Deadlines are checked against your
   device’s clock.</p>
</aside>

<main class="stage">
  <div id="map" role="region" aria-label="Map of Hackney showing planning
       applications and road changes open for objection. Everything shown is
       also listed in the panel."></div>
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
  var M={mapdata};
  var today;
  try{{today=new Intl.DateTimeFormat('en-CA',{{timeZone:'Europe/London',
    year:'numeric',month:'2-digit',day:'2-digit'}}).format(new Date());}}
  catch(e){{var n=new Date();today=n.getFullYear()+'-'+('0'+(n.getMonth()+1)).slice(-2)+'-'+('0'+n.getDate()).slice(-2);}}
  // No deadline means the council has not published one, not
  // that it has passed.
  var shut=function(c){{return !!c && today>c;}};
  var $=function(id){{return document.getElementById(id);}};

  // ---- map ---------------------------------------------------------------
  var protocol=new pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles',protocol.tile);
  var tiles='pmtiles://'+new URL({tilesurl},location.href).href;
  // The Protomaps light style, recoloured to the page: warm paper for land,
  // quiet greens and blues, so the markers keep the strong colours.
  var flavor=Object.assign({{}},basemaps.namedFlavor('light'),{{
    background:'#f4f3ee', earth:'#eceae2', water:'#cfdcdd',
    park_a:'#dee2cf', park_b:'#d7dcc5', wood_a:'#dee2cf', wood_b:'#d7dcc5',
    scrub_a:'#e2e4d3', scrub_b:'#dde0cc', school:'#e6e0cc',
    hospital:'#e6dcd6', industrial:'#e4e2da', pedestrian:'#efede6',
    buildings:'#dcd8cc', railway:'#c4c0b5', boundaries:'#bcb8ad',
    highway:'#ffffff', major:'#ffffff', link:'#ffffff',
    minor_a:'#fafaf7', minor_b:'#fafaf7', minor_service:'#f6f4ee',
    other:'#f6f4ee',
    highway_casing_early:'#97928a', highway_casing_late:'#97928a',
    major_casing_early:'#b2ada0', major_casing_late:'#b2ada0',
    minor_casing:'#d3d0c5', minor_service_casing:'#d3d0c5',
    roads_label_minor:'#5b5b57', roads_label_minor_halo:'#fafaf7',
    roads_label_major:'#3d352b', roads_label_major_halo:'#fafaf7',
    subplace_label:'#6f6a52', subplace_label_halo:'#eceae2',
    address_label:'#8a867a', address_label_halo:'#eceae2'
  }});
  var map=new maplibregl.Map({{
    container:'map',
    bounds:M.bounds, fitBoundsOptions:{{padding:24}},
    maxBounds:[[-0.16,51.49],[0.035,51.605]],
    minZoom:10.5, maxZoom:19,
    attributionControl:{{compact:true}},
    dragRotate:false, pitchWithRotate:false, touchPitch:false,
    style:{{
      version:8,
      glyphs:'https://protomaps.github.io/basemaps-assets/fonts/{{fontstack}}/{{range}}.pbf',
      sprite:'https://protomaps.github.io/basemaps-assets/sprites/v4/light',
      sources:{{
        protomaps:{{type:'vector',url:tiles,
          attribution:'<a href="https://protomaps.com">Protomaps</a> &copy; '+
            '<a href="https://openstreetmap.org/copyright">OpenStreetMap</a>'}},
        wards:{{type:'geojson',data:M.wards}},
        outline:{{type:'geojson',data:M.outline||M.wards}},
        veil:{{type:'geojson',data:M.veil}},
        lines:{{type:'geojson',data:M.lines}},
        landmarks:{{type:'geojson',data:M.landmarks}}
      }},
      layers:basemaps.layers('protomaps',flavor,{{lang:'en'}})
    }}
  }});
  map.touchZoomRotate.disableRotation();
  map.on('load',function(){{
    // Landmark tints go under the base map's labels, so names stay
    // readable; everything else goes on top.
    var firstLabel=(map.getStyle().layers.filter(function(l){{
      return l.type==='symbol';}})[0]||{{}}).id;
    map.addLayer({{id:'landmarks',type:'fill',source:'landmarks',minzoom:13.5,
      paint:{{'fill-color':['get','f'],'fill-opacity':.9}}}},firstLabel);
    map.addLayer({{id:'veil',type:'fill',source:'veil',
      paint:{{'fill-color':'#f4f3ee','fill-opacity':.74}}}});
    map.addLayer({{id:'wards',type:'line',source:'wards',
      paint:{{'line-color':'#a9a598','line-width':['interpolate',['linear'],
        ['zoom'],11,.7,15,1.4],'line-dasharray':[3,2]}}}});
    map.addLayer({{id:'outline',type:'line',source:'outline',
      layout:{{'line-join':'round'}},
      paint:{{'line-color':'#4a4a46','line-width':2}}}});
    map.addLayer({{id:'lines',type:'line',source:'lines',
      layout:{{'line-cap':'round','line-join':'round'}},
      paint:{{'line-color':'#a8322a','line-opacity':.55,
        'line-width':['interpolate',['linear'],['zoom'],11,2.5,16,6]}}}});
  }});

  // Ward names, in the page's own typeface rather than the map's.
  M.wardLabels.forEach(function(w){{
    var el=document.createElement('div');
    el.className='wl'; el.innerHTML=w.html;
    new maplibregl.Marker({{element:el}}).setLngLat([w.lon,w.lat]).addTo(map);
  }});
  var stage=document.querySelector('.stage');
  function near(){{
    if(map.getZoom()>=14.3) stage.setAttribute('data-near','');
    else stage.removeAttribute('data-near');
  }}
  map.on('zoom',near); near();

  // The markers are ordinary page elements, so they keep the page's
  // styling, take keyboard focus and answer to the filters below.
  var pins=M.pins.map(function(p){{
    var el=document.createElement('div');
    el.className='pin '+p.kind+(p.close?'':' nodl');
    el.setAttribute('data-id',p.id);
    el.setAttribute('data-close',p.close);
    el.innerHTML=p.svg;
    new maplibregl.Marker({{element:el}}).setLngLat([p.lon,p.lat]).addTo(map);
    var d=D[p.id]||{{}};
    el.setAttribute('tabindex','0');
    el.setAttribute('role','button');
    el.setAttribute('aria-label',(d.title||'')+', '+(d.sub||''));
    return el;
  }});

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
  var nd={n_nodeadline};
  $('n-plan').textContent=np+(np===1?' planning application':' planning applications');
  $('n-road').textContent='and '+nt+(nt===1?' road scheme':' road schemes')+
    ' with a published deadline';

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
      (d.approxTxt?' · '+d.approxTxt:'')+'</p>'+items+
      '<p class="close">'+(d.noDeadline?d.closeTxt:
        (closed?'Closed ':'Comment by ')+d.closeTxt)+'</p>'+
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

  pins.forEach(function(p){{
    p.addEventListener('click',function(e){{
      e.stopPropagation(); show(p.getAttribute('data-id'),p);
    }});
  }});
  // A click on the map itself, not on a marker, puts the card away.
  map.on('click',function(e){{
    var t=e.originalEvent&&e.originalEvent.target;
    if(t&&t.closest&&t.closest('.pin')) return;
    hideCard();
  }});

  // ---- zoom --------------------------------------------------------------
  $('zin').addEventListener('click',function(){{map.zoomIn();}});
  $('zout').addEventListener('click',function(){{map.zoomOut();}});
  $('zreset').addEventListener('click',function(){{
    map.fitBounds(M.bounds,{{padding:24}});
  }});

  // ---- filters ---------------------------------------------------------
  var kind='all';
  var showNoDl=false;
  function matches(p){{
    if(shut(p.getAttribute('data-close'))) return false;
    // Applications with no published deadline are off by default: there
    // are far more of them than there are dated ones, and they would
    // bury what a reader can actually act on.
    if(!showNoDl && p.classList.contains('nodl')) return false;
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
  $('n-nodl').textContent=nd;
  $('k-nodl').addEventListener('change',function(e){{
    showNoDl=e.target.checked; refilter();
  }});
  // The list keeps everything; only the map hides them.
  function listNoDl(){{
    document.querySelectorAll('.row').forEach(function(el){{
      if(el.querySelector('.nodl')) el.style.display=showNoDl?'':'none';
    }});
  }}
  $('k-nodl').addEventListener('change',listNoDl);
  listNoDl();
  refilter();        // apply the defaults before anything is touched
  [['k-all','all'],['k-plan','plan'],['k-road','road']].forEach(function(pair){{
    $(pair[0]).addEventListener('click',function(){{
      kind=pair[1];
      ['k-all','k-plan','k-road'].forEach(function(id){{
        $(id).setAttribute('aria-pressed', id===pair[0]);
      }});
      refilter();
    }});
  }});
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
