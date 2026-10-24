#!/usr/bin/env bash
# weekly.sh: the whole weekly rebuild of objections.html. GitHub runs it
# every week (see .github/workflows/weekly-objections.yml); you can also
# run it on your Mac with "bash weekly.sh" in the folder with the scripts.
#
# The road changes and the planning list are fetched separately, so a
# failure in one does not hold back the other:
#
#   - Each source that comes in good is saved in last-good/.
#   - If a source fails, the page is built with its last good copy instead,
#     and the failure is written to fallbacks.txt, which makes the GitHub
#     run report a failure and email you, even though the page is published.
#   - The run stops without publishing only if there is no saved copy to
#     fall back on, or the page itself cannot be built.

set -uo pipefail
cd "$(dirname "$0")"

SAVED=last-good
mkdir -p "$SAVED" traffic_probe
TODAY=$(date +%F)
: > fallbacks.txt

warn() { echo "!! $1"; echo "$1" >> fallbacks.txt; }
stop() { echo; echo "STOPPED, NOT PUBLISHED: $1"; echo "The previous page stays live."; exit 1; }
saved_on() { cat "$1.date" 2>/dev/null || echo "an earlier run"; }

# ---- road changes ---------------------------------------------------------
echo "== road changes"
TFILE="traffic_probe/traffic-geometry-$TODAY.geojson"
rm -f "$TFILE"
if python3 traffic_ingest.py; then
  traffic=$(python3 weekly_check.py traffic "$TFILE" "$SAVED/traffic-geometry.geojson")
else
  traffic=failed
fi
case "$traffic" in
  fresh)
    TUSE="$TFILE" ;;
  none-open)
    echo "no road schemes open, and none of last week's is still open: publishing with none"
    echo '{"type":"FeatureCollection","features":[]}' > no-road-changes.geojson
    TUSE=no-road-changes.geojson ;;
  *)
    [ -f "$SAVED/traffic-geometry.geojson" ] ||
      stop "the road changes step failed and there is no saved copy to fall back on"
    if [ "$traffic" = failed ]; then why="the road changes step failed"
    else why="no road schemes were found, though last good ones have not closed yet"; fi
    warn "Road changes: $why. Published with the road changes saved on $(saved_on "$SAVED/traffic-geometry.geojson")."
    TUSE="$SAVED/traffic-geometry.geojson" ;;
esac

# ---- planning -------------------------------------------------------------
echo
echo "== planning"
# The planning steps run together in a subshell that stops at the first
# failure. (Inside an "if", bash would ignore set -e, so the result is
# taken afterwards.)
(
  set -e
  if ls hackney-planning-embed_*.html >/dev/null 2>&1; then
    python3 parse_planning.py hackney-planning-embed_*.html --out notices-from-embeds.csv
    python3 weekly_check.py merge-notices notices-from-embeds.csv planning-from-notices.csv
  fi
  python3 fetch_weekly_lists.py --weeks 12
  python3 parse_weekly_lists.py --deadlines planning-from-notices.csv
  python3 weekly_check.py planning
)
if [ $? -eq 0 ]; then
  planning=fresh
else
  planning=failed
  [ -f "$SAVED/planning-applications.csv" ] ||
    stop "the planning step failed and there is no saved copy to fall back on"
  cp "$SAVED/planning-applications.csv" planning-applications.csv
  rm -f data-current-to.txt
  [ -f "$SAVED/data-current-to.txt" ] && cp "$SAVED/data-current-to.txt" data-current-to.txt
  warn "Planning: the planning step failed (see the log). Published with the planning list saved on $(saved_on "$SAVED/planning-applications.csv")."
fi

# ---- the page -------------------------------------------------------------
echo
echo "== the page"
python3 build_objections_page.py --fullpage --traffic "$TUSE" \
  --tiles-url "${TILES_URL:-hackney.pmtiles}" ||
  stop "the page could not be built"
python3 weekly_check.py page || stop "the page failed its checks"

# Keep this week's good sources for next time.
if [ "$planning" = fresh ]; then
  cp planning-applications.csv "$SAVED/"
  date "+%-d %B %Y" > "$SAVED/planning-applications.csv.date"
  rm -f "$SAVED/data-current-to.txt"
  [ -f data-current-to.txt ] && cp data-current-to.txt "$SAVED/"
  python3 weekly_check.py save-stats
fi
if [ "$traffic" = fresh ]; then
  cp "$TFILE" "$SAVED/traffic-geometry.geojson"
  date "+%-d %B %Y" > "$SAVED/traffic-geometry.geojson.date"
fi
rm -f no-road-changes.geojson

echo
if [ -s fallbacks.txt ]; then
  echo "Published, but with a fallback:"
  sed 's/^/  /' fallbacks.txt
else
  echo "Published: road changes and planning both fresh."
fi
exit 0
