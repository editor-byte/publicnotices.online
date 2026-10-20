#!/usr/bin/env python3
"""
parse_planning.py

Turns published planning embeds into structured rows, so publishing the
fortnightly roundup *is* the ingest. No separate CSV to maintain.

    python3 parse_planning.py hackney-planning-embed-*.html

Writes planning-applications.csv, one row per application, deduplicated on
reference across every file given. Re-run it with the whole set each time
and it rebuilds the lot.

What it reads from each embed:
  reference, register link and id, address, postcode, postcode district,
  category and designation, the official proposal text, whether the
  application was on the council's weekly list, and the notice date from
  the map's aria-label. The comment deadline is the notice date plus 21
  days, which is the only publicly published date by which a resident must
  object — Hackney's own register never shows one.

Standard library only.
"""

import argparse
import csv
import datetime as dt
import glob
import html
import re
import sys

MONTHS = ("January February March April May June July August September "
          "October November December").split()

FIELDS = [
    "reference", "notice_date", "comment_deadline", "deadline_basis",
    "publication", "provenance", "address", "postcode", "postcode_district",
    "category", "designation", "proposal_official", "register_url",
    "register_app_id", "on_weekly_list", "source_file",
]


def strip(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def notice_date(doc):
    m = re.search(r"advertised on (\d{1,2}) (\w+) (\d{4})", doc)
    if not m:
        return None
    d, mon, y = m.group(1), m.group(2), m.group(3)
    if mon not in MONTHS:
        return None
    return dt.date(int(y), MONTHS.index(mon) + 1, int(d))


def parse(doc, fname):
    nd = notice_date(doc)
    if not nd:
        print(f"  {fname}: no notice date in the map aria-label — skipped")
        return []
    deadline = nd + dt.timedelta(days=21)
    rows = []

    for grp in re.findall(r'<div class="hk-wgrp">(.*?)</div>', doc, re.S):
        dm = re.search(r"<h2>([^<\s]+)", grp)
        district = strip(dm.group(1)) if dm else ""
        for li in re.findall(r"<li>(.*?)</li>", grp, re.S):
            ref_m = re.search(r'class="hk-pref"[^>]*>(?:<a[^>]*>)?([^<]+)', li)
            ref = strip(ref_m.group(1)) if ref_m else ""
            if not ref:
                continue

            url_m = re.search(r'class="hk-reflink" href="([^"]+)"', li)
            url = html.unescape(url_m.group(1)) if url_m else ""
            idm = re.search(r"[?&]id=(\d+)", url)

            addr_m = re.search(r'class="hk-paddr">(.*?)<span class="hk-cat"', li, re.S)
            addr = strip(addr_m.group(1)) if addr_m else ""
            pc_m = re.search(r"([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\s*$", addr)
            postcode = pc_m.group(1).strip() if pc_m else ""

            cat_m = re.search(r'class="hk-cat">(.*?)</span>', li, re.S)
            cat_full = strip(cat_m.group(1)) if cat_m else ""
            if "·" in cat_full:
                category, designation = [p.strip() for p in cat_full.split("·", 1)]
            else:
                category, designation = cat_full, ""

            prop_m = re.search(r"Proposal:\s*(.*?)</span>", li, re.S)
            proposal = strip(prop_m.group(1)) if prop_m else ""

            rows.append({
                "reference": ref,
                "notice_date": nd.isoformat(),
                "comment_deadline": deadline.isoformat(),
                "deadline_basis": "notice_21_day",
                "publication": "Hackney Gazette",
                "provenance": "print_other",
                "address": addr,
                "postcode": postcode,
                "postcode_district": district,
                "category": category,
                "designation": designation,
                "proposal_official": proposal,
                "register_url": url,
                "register_app_id": idm.group(1) if idm else "",
                "on_weekly_list": "not_yet" if not url else "yes",
                "source_file": fname,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="embed HTML files, or a glob")
    ap.add_argument("--out", default="planning-applications.csv")
    args = ap.parse_args()

    paths = []
    for f in args.files:
        paths += sorted(glob.glob(f)) or [f]

    byref, order = {}, []
    for p in paths:
        try:
            doc = open(p, encoding="utf-8").read()
        except OSError as e:
            print(f"  cannot read {p}: {e}")
            continue
        rows = parse(doc, p.split("/")[-1])
        print(f"  {p.split('/')[-1]}: {len(rows)} applications")
        for r in rows:
            ref = r["reference"]
            if ref in byref:
                # Re-advertised. One record, later deadline governs.
                old = byref[ref]
                if r["notice_date"] > old["notice_date"]:
                    r["source_file"] = old["source_file"] + "; " + r["source_file"]
                    byref[ref] = r
                else:
                    old["source_file"] += "; " + r["source_file"]
                print(f"    re-advertised: {ref}")
            else:
                byref[ref] = r
                order.append(ref)

    rows = [byref[r] for r in order]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    today = dt.date.today()
    live = [r for r in rows if r["comment_deadline"] >= today.isoformat()]
    unlinked = [r for r in rows if r["on_weekly_list"] == "not_yet"]
    nopc = [r for r in rows if not r["postcode"]]

    print(f"\n{len(rows)} applications -> {args.out}")
    print(f"  still open for comment today: {len(live)}")
    print(f"  not yet on the weekly list:   {len(unlinked)}")
    if nopc:
        print(f"  WITHOUT A POSTCODE:           {len(nopc)} — check these")
        for r in nopc[:5]:
            print(f"    {r['reference']}  {r['address'][:60]}")
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print("  categories: " + ", ".join(f"{k} {v}" for k, v in
                                       sorted(cats.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
