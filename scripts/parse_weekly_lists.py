#!/usr/bin/env python3
"""
parse_weekly_lists.py

Builds the planning rows from Hackney's weekly consultation sheets instead
of from the Gazette's printed notices.

    python3 fetch_weekly_lists.py --weeks 12
    python3 parse_weekly_lists.py

Writes planning-applications.csv, the same shape build_objections_page.py
already reads.

What the sheets give, automatically: reference, address, proposal,
application type, ward, and the council's own link to each application.

What they do not give: a date by which a resident must comment. The sheets
carry only Registration Date and Valid Date. So unless a deadline is known
from a printed notice, each row is marked as having none published — which
is the truth, and the point.

    --deadlines FILE   an earlier planning-applications.csv, built from the
                       printed notices by parse_planning.py. Deadlines are
                       carried across by reference so past work is kept.

Standard library only.
"""

import argparse
import csv
import datetime as dt
import glob
import os
import re
import sys

FIELDS = [
    "reference", "notice_date", "comment_deadline", "deadline_basis",
    "publication", "provenance", "address", "postcode", "postcode_district",
    "category", "designation", "proposal_official", "register_url",
    "register_app_id", "on_weekly_list", "ward", "source_file",
]

# Column names drift between weeks — "Location" becomes "4 - Location"
# becomes "Location Description" — so match on meaning, not position.
WANT = {
    "reference": ("application reference", "reference", "application"),
    "address": ("location description", "location", "address", "site"),
    "proposal": ("proposal", "description"),
    "type": ("application type", "type"),
    "url": ("public url", "url", "link"),
    "ward": ("ward",),
    "valid": ("valid date",),
    "registered": ("registration date", "registered"),
}

# Hackney's application types, mapped to the categories the paper uses.
# Anything unmatched is reported rather than silently filed.
TYPE_CATEGORY = [
    # Procedural applications first: these follow a permission already
    # granted and mostly carry no public consultation, so filing them as
    # building proposals would overstate what is up for comment.
    ("discharge of condition", "Conditions and amendments"),
    ("removal/variation", "Conditions and amendments"),
    ("variation of condition", "Conditions and amendments"),
    ("non-material amendment", "Conditions and amendments"),
    ("reserved matters", "Conditions and amendments"),
    ("deed of variation", "Conditions and amendments"),
    ("s106", "Conditions and amendments"),
    ("listed building", "Listed building consent"),
    ("advertis", "Signs and shopfronts"),
    ("advert", "Signs and shopfronts"),
    ("change of use", "Change of use"),
    ("full planning", "Extensions and alterations"),
    ("householder", "Extensions and alterations"),
    ("outline", "New homes"),
    ("major", "Major development"),
    ("prior approval", "Extensions and alterations"),
    ("lawful", "Extensions and alterations"),
    ("conservation area consent", "Listed building consent"),
    ("certificate", "Extensions and alterations"),
    ("tree", "Infrastructure"),
    ("telecom", "Infrastructure"),
]


def norm(h):
    return re.sub(r"^\s*\d+[A-Za-z]?\s*-\s*", "", (h or "")).strip().lower()


def map_columns(header):
    out = {}
    cols = [norm(h) for h in header]
    for key, names in WANT.items():
        for i, c in enumerate(cols):
            if any(c == n for n in names):
                out[key] = i
                break
        if key not in out:
            for i, c in enumerate(cols):
                if any(n in c for n in names):
                    out[key] = i
                    break
    return out


def find_header(rows):
    for i, r in enumerate(rows[:8]):
        joined = " ".join(norm(c) for c in r)
        if "application reference" in joined or (
                "reference" in joined and "proposal" in joined):
            return i
    return 0


def categorise(t, unmapped):
    low = (t or "").lower()
    for key, cat in TYPE_CATEGORY:
        if key in low:
            return cat
    if low:
        unmapped.add(t)
    return "Extensions and alterations"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="weekly_lists")
    ap.add_argument("--deadlines", default="planning-applications.csv",
                    help="earlier CSV built from printed notices")
    ap.add_argument("--out", default="planning-applications.csv")
    args = ap.parse_args()

    # Deadlines already known from the printed notices.
    known = {}
    if os.path.exists(args.deadlines):
        with open(args.deadlines, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("comment_deadline"):
                    known[r["reference"]] = (r["comment_deadline"],
                                             r.get("notice_date", ""),
                                             r.get("deadline_basis", ""))
        print(f"{len(known)} deadlines carried over from {args.deadlines}")

    files = sorted(glob.glob(os.path.join(args.dir, "*.csv")))
    if not files:
        print(f"No sheets in {args.dir}/. Run fetch_weekly_lists.py first.")
        return 1

    byref, order, unmapped, nopc = {}, [], set(), []
    for path in files:
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
        if not rows:
            continue
        h = find_header(rows)
        cols = map_columns(rows[h])
        missing = [k for k in ("reference", "address", "proposal") if k not in cols]
        if missing:
            print(f"  {os.path.basename(path):<48}cannot map {missing} — skipped")
            continue

        n = 0
        for r in rows[h+1:]:
            def cell(key):
                i = cols.get(key)
                return r[i].strip() if i is not None and i < len(r) else ""

            ref = cell("reference")
            if not ref or not re.match(r"^\d{4}/\d+", ref):
                continue
            addr = cell("address")
            pc = re.search(r"([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b",
                           addr.upper())
            postcode = re.sub(r"\s+", " ", pc.group(1)).strip() if pc else ""
            ward = cell("ward")
            if not postcode:
                nopc.append((ref, addr, ward))
            dl, nd, basis = known.get(ref, ("", "", "not_published"))
            byref[ref] = {
                "reference": ref,
                "notice_date": nd,
                "comment_deadline": dl,
                "deadline_basis": basis,
                "publication": "Hackney Council weekly list",
                "provenance": "register",
                "address": addr,
                "postcode": postcode,
                "postcode_district": (postcode.split(" ")[0] if postcode
                                      else ward),
                "ward": ward,
                "category": categorise(cell("type"), unmapped),
                "designation": ward,
                "proposal_official": cell("proposal"),
                "register_url": cell("url"),
                "register_app_id": "",
                "on_weekly_list": "yes",
                "source_file": os.path.basename(path),
            }
            if ref not in order:
                order.append(ref)
            n += 1
        print(f"  {os.path.basename(path):<48}{n} applications")

    rows = [byref[r] for r in order]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    withdl = sum(1 for r in rows if r["comment_deadline"])
    print(f"\n{len(rows)} applications -> {args.out}")
    print(f"  with a published comment deadline: {withdl}")
    print(f"  with none:                         {len(rows)-withdl}")
    print(f"  with a register link:              "
          f"{sum(1 for r in rows if r['register_url'])}")
    print(f"  without a postcode:                {len(nopc)}")
    for ref, a, w in nopc[:5]:
        print(f"      {ref}  {a[:46]:<48}ward: {w or 'none'}")
    if nopc:
        wardless = [r for r in nopc if not r[2]]
        print(f"  of those, {len(nopc)-len(wardless)} have a ward to fall "
              f"back on, {len(wardless)} have nothing")
    if unmapped:
        print("\n  application types not mapped to a category "
              "(filed as extensions):")
        for t in sorted(unmapped)[:12]:
            print(f"      {t}")
    print("\nRows without a deadline are shown as having none published by")
    print("the council, which is what the sheets actually say.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
