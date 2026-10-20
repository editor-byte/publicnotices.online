# publicnotices.online

What you can object to in Hackney right now: planning applications and road
changes currently open for comment, each with the deadline that applies to it.

Rebuilt every morning by GitHub Actions and published to GitHub Pages. No
server, no database, no API keys, no tile provider. Nothing about a reader
leaves the page.

## Layout

```
scripts/        traffic_ingest.py, parse_planning.py, build_objections_page.py
data/           hackney-wards.json, hackney-roads.json, hackney-postcodes.json
embeds/         the published planning roundup embeds, one per notice
public/         built output — generated, not edited
CNAME           the custom domain
```

## What happens each morning

1. `traffic_ingest.py` queries Hackney's Traffweb WFS for every traffic order
   currently out for objection, and writes the geometry and dates.
2. `parse_planning.py` reads the published planning embeds in `embeds/` and
   produces one row per application, with the comment deadline set at 21 days
   from the notice date.
3. `build_objections_page.py` draws the map and the lists.

Only items still open are mapped. The page also rechecks every deadline
against the reader's own clock, so a stale build still reports closures
correctly rather than inviting comments on something that has shut.

## The fortnightly job

When a new planning roundup is published, save its embed HTML into `embeds/`
and push. The workflow runs on any change there, so the page updates within a
few minutes. That is the only manual step.

Traffic needs nothing: it comes straight from the council's own feed.

## Running it locally

Everything is standard library Python 3, no dependencies:

```
python3 scripts/traffic_ingest.py
python3 scripts/parse_planning.py embeds/*.html
python3 scripts/build_objections_page.py --fullpage
```

## Rebuilding the data files

These change rarely and are committed rather than fetched on every run.

- `hackney-wards.json` — `make_wards` from the GLA/ONS London ward
  boundaries. Only changes when ward boundaries do.
- `hackney-roads.json` — `make_roads.py` from OS Open Roads. Worth refreshing
  when OS publish a new edition, roughly twice a year.
- `hackney-postcodes.json` — `make_postcodes.py` from OS Code-Point Open.
  Refresh occasionally so new postcodes resolve exactly rather than falling
  back to a sector centroid.

## Sources and licensing

- Traffic orders: Hackney Council, via Traffweb.
- Planning applications: advertised by Hackney Council, reported in the
  Hackney Citizen. The comment deadline is 21 days from the notice date,
  which is the only publicly published date by which a comment must be made —
  the council's own register shows when it intends to decide, not when you
  can object.
- Ward boundaries: GLA and ONS London ward boundaries.
- Roads and postcode positions: OS Open Roads and OS Code-Point Open,
  © Crown copyright and database right, Open Government Licence v3.

## Known limits

- Markers sit at the postcode, not the building, and are nudged apart where
  they would overlap, with a hairline back to the true position.
- Three postcodes in the current notices are absent from Code-Point Open and
  are placed at sector level, accurate to a few hundred metres. The page says
  so against each.
- One site has no postcode in the notice at all and is listed without a pin.
  Add a grid reference to `MANUAL_PLACE` in `build_objections_page.py` to
  place it.
- The traffic data records which bays and restrictions an order alters, but
  not which is being removed and which created. The draft order is the place
  to check.
