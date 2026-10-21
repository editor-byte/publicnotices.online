#!/usr/bin/env python3
"""
fetch_weekly_lists.py

Hackney publishes its weekly planning consultation lists as Google Sheets,
linked from the council's planning consultation page. A shared sheet can be
pulled as CSV, so the whole thing is machine-readable without a PDF, a
notice or a roundup.

    python3 fetch_weekly_lists.py            # the 6 most recent weeks
    python3 fetch_weekly_lists.py --all      # everything in the archive
    python3 fetch_weekly_lists.py --weeks 20

Saves each week to weekly_lists/ and prints what the columns actually are,
which is the question that decides whether this can replace the Gazette
PDFs: does the list carry a consultation end date?

Standard library only.
"""

import argparse
import csv
import io
import os
import re
import sys
import urllib.error
import urllib.request

PAGE = "https://www.hackney.gov.uk/planning-and-building/planning-consultation"
UA = "notices-research/1.0 (Hackney Citizen)"
OUT = "weekly_lists"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def find_sheets(html):
    """Every sheet link on the page, with the date range from its text."""
    out, seen = [], set()
    pat = re.compile(
        r'href="https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)[^"]*"'
        r'[^>]*>([^<]+)</a>')
    for m in pat.finditer(html):
        sid, label = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if sid in seen:
            continue
        seen.add(sid)
        out.append((sid, label))
    return out


def slug(label):
    s = re.sub(r"\(.*?\)", "", label).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:70] or "list"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    print(f"reading {PAGE}")
    sheets = find_sheets(get(PAGE))
    print(f"{len(sheets)} sheets linked\n")
    if not args.all:
        sheets = sheets[:args.weeks]

    os.makedirs(OUT, exist_ok=True)
    headers_seen, got = {}, 0
    for sid, label in sheets:
        # A link-shared sheet exports as CSV without a key or sign-in.
        url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
        try:
            body = get(url)
        except urllib.error.HTTPError as e:
            print(f"  {label[:52]:<54}HTTP {e.code} — not shared for export")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {label[:52]:<54}{e}")
            continue

        rows = list(csv.reader(io.StringIO(body)))
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            print(f"  {label[:52]:<54}empty")
            continue

        path = os.path.join(OUT, slug(label) + ".csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        got += 1

        # The header is not always the first row: these sheets often carry a
        # title row or two above it.
        hdr_i = 0
        for i, r in enumerate(rows[:6]):
            joined = " ".join(r).lower()
            if "application" in joined or "reference" in joined or "proposal" in joined:
                hdr_i = i
                break
        hdr = tuple(c.strip() for c in rows[hdr_i] if c.strip())
        headers_seen.setdefault(hdr, []).append(label)
        print(f"  {label[:52]:<54}{len(rows)-hdr_i-1} rows")

    print(f"\nsaved {got} sheets to {OUT}/\n")
    print("=" * 74)
    print("COLUMNS")
    print("=" * 74)
    for hdr, labels in headers_seen.items():
        print(f"\n{len(labels)} sheet(s) with these columns:")
        for c in hdr:
            print(f"    {c}")
        # The question this whole exercise exists to answer.
        blob = " ".join(hdr).lower()
        date_cols = [c for c in hdr if any(k in c.lower() for k in
                     ("date", "expiry", "deadline", "comment", "consult", "by"))]
        print("  date-ish columns: " + (", ".join(date_cols) or "NONE"))
        if not date_cols:
            print("  -> no consultation deadline here; the 21-day press notice")
            print("     date would still have to come from somewhere else.")

    print("\nIf the columns differ between weeks, the parser will need to map")
    print("them rather than assume a fixed order.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
