#!/usr/bin/env python3
"""
weekly_check.py: the checks and bookkeeping weekly.sh needs besides the
existing scripts. Each job is one command:

    python3 weekly_check.py merge-notices NEW.csv planning-from-notices.csv

  Adds the deadlines parse_planning.py has just read from the roundup
  embeds to planning-from-notices.csv. It only ever adds or updates: a
  notice whose embed file is missing this time is kept, never dropped.
  Where an application appears in both, the later notice wins.

    python3 weekly_check.py planning

  Is this week's planning-applications.csv fit to publish? Fails if there
  are no applications at all, or if the deadlines from the notices have
  gone missing.

    python3 weekly_check.py traffic TODAYS.geojson SAVED.geojson

  Prints one word about this week's road changes: "fresh" if any were
  found; "none-open" if none were found and none of the saved ones is
  still open, which is believable; "suspicious" if none were found while
  saved schemes have not yet reached their closing dates, which is not.

    python3 weekly_check.py page

  Fails if objections.html is missing, too small, or has no map or
  markers in it.

    python3 weekly_check.py save-stats

  Records this week's planning figures in last-good/stats.json, for next
  week's comparison.

Standard library only.
"""

import csv
import datetime as dt
import json
import pathlib
import sys

MIN_APPLICATIONS = 1          # stop only if there are none at all
MIN_PAGE_KB = 300             # the real page is about 1,000 kB
STATS = pathlib.Path("last-good/stats.json")
TODAY = dt.date.today().isoformat()


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def fail(msg):
    print("FAILED: " + msg)
    sys.exit(1)


def merge_notices(new_path, store_path):
    fields, new = read_rows(new_path)
    store = pathlib.Path(store_path)
    old = read_rows(store)[1] if store.exists() else []
    byref = {r["reference"]: r for r in old}
    order = [r["reference"] for r in old]
    added = updated = 0
    for r in new:
        ref = r["reference"]
        if ref not in byref:
            byref[ref] = r
            order.append(ref)
            added += 1
        elif r["notice_date"] > byref[ref]["notice_date"]:
            byref[ref] = r
            updated += 1
    with open(store, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(byref[ref] for ref in order)
    print(f"notices: {len(order)} in {store_path} "
          f"({added} added, {updated} re-advertised, none dropped)")


def planning_figures():
    rows = read_rows("planning-applications.csv")[1]
    with_deadline = sum(1 for r in rows if r.get("comment_deadline"))
    open_now = sum(1 for r in rows if (r.get("comment_deadline") or "") >= TODAY)
    return len(rows), with_deadline, open_now


def planning():
    if not pathlib.Path("planning-applications.csv").exists():
        fail("planning-applications.csv was not made")
    n, with_deadline, open_now = planning_figures()
    last = json.loads(STATS.read_text()) if STATS.exists() else {}
    print(f"planning: {n} applications, {with_deadline} with a deadline, "
          f"{open_now} open today" +
          (f"  (last good {last['date']}: {last['applications']}, "
           f"{last['with_deadline']} with a deadline)" if last else ""))
    if n < MIN_APPLICATIONS:
        fail("there are no planning applications at all")
    if pathlib.Path("planning-from-notices.csv").exists():
        notices = read_rows("planning-from-notices.csv")[1]
        since = (dt.date.today() - dt.timedelta(weeks=8)).isoformat()
        if with_deadline == 0 and any((r.get("comment_deadline") or "") >= since
                                      for r in notices):
            fail("no deadlines were carried across from planning-from-notices.csv")
    if last and with_deadline < 0.5*last["with_deadline"]:
        fail(f"only {with_deadline} applications with a deadline, down from "
             f"{last['with_deadline']}")


def features(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("features", [])
    except ValueError:
        return []


def traffic(todays, saved):
    if features(todays):
        print("fresh")
    elif any((f.get("properties", {}).get("date_close") or "") >= TODAY
             for f in features(saved)):
        print("suspicious")
    else:
        print("none-open")


def page():
    p = pathlib.Path("objections.html")
    if not p.exists():
        fail("objections.html was not made")
    kb = p.stat().st_size // 1024
    text = p.read_text(encoding="utf-8")
    if kb < MIN_PAGE_KB:
        fail(f"objections.html is only {kb} kB; the real page is about 1,000 kB")
    if "maplibregl" not in text or '"pins":[{' not in text:
        fail("objections.html has no map or no markers in it")
    print(f"page: {kb} kB, map and markers present")


def save_stats():
    n, with_deadline, open_now = planning_figures()
    STATS.parent.mkdir(exist_ok=True)
    STATS.write_text(json.dumps({"date": TODAY, "applications": n,
                                 "with_deadline": with_deadline,
                                 "open_today": open_now}, indent=1) + "\n")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "merge-notices":
        merge_notices(a[1], a[2])
    elif a == ["planning"]:
        planning()
    elif len(a) == 3 and a[0] == "traffic":
        traffic(a[1], a[2])
    elif a == ["page"]:
        page()
    elif a == ["save-stats"]:
        save_stats()
    else:
        print(__doc__)
        sys.exit(2)
