# Geographic data collection scripts

These scripts use Python's standard library only. They do not assign landmark coordinates automatically and do not create straight-line routes. Network access is required for OSM/Nominatim/Overpass and Valhalla operations.

## PowerShell commands

Run from the project root:

```powershell
python --version
$env:JAUNT_USER_AGENT = 'JauntRouteRecommender/0.1 (your contact email or project URL)'
$env:JAUNT_CLIENT_ID = 'jaunt-route-recommender-research'
$env:JAUNT_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
python .\backend\scripts\collect_geodata.py --landmarks 'grand_central_terminal,pershing_square_building,one_vanderbilt,bryant_park'
```

The active nine-landmark set contains both NYPL's Stephen A. Schwarzman Building and 1 Vanderbilt. OSM name aliases for 1 Vanderbilt are “One Vanderbilt”, “1 Vanderbilt”, “One Vanderbilt Avenue”, and “1 Vanderbilt Avenue”. Its query requests named building ways and relations and uses `out body center geom;` to preserve returned geometry, tags, and representative center. Any result remains an unverified candidate. Other architectural buildings use named building ways and relations. Bryant Park is queried only as a named park polygon carrying park-related tags (`leisure`, `landuse`, `boundary`, or `tourism`); nodes and transit features are excluded. Same-name way/relation representations within 15 m are grouped while preserving their OSM IDs; unnamed nearby buildings are kept separate. Exact names and scores are candidate-ranking evidence only, never automatic approval.

The collector makes a separate Overpass request for each landmark by default (`--batch-size 1`; larger values are rejected). `--landmarks` accepts a comma-separated subset of known IDs. Existing successful candidate records are preserved and are not requested again, even if included in the selection. At collection time, the endpoint is read from `JAUNT_OVERPASS_URL`, defaulting to `https://overpass-api.de/api/interpreter`; this lets a PowerShell environment override take effect without changing source code. The raw response for each landmark is stored separately in `backend/data/raw/osm/`; results are deduplicated by OSM type and ID. Transient HTTP 502/503/504 and network timeout failures are retried up to three attempts with 2-second then 4-second backoff. Other errors are recorded without retry. A failed request is recorded in `overpass_batches`, and that landmark receives `unresolved_batch_failed`. A successful request with no matching feature marks the landmark `unresolved_no_matches`. A failed request is never treated as a successful empty response. Selective runs update only selected unresolved records, preserving unselected records and their candidate entries. Results are written to `backend/data/landmark_candidates_v2.json` only after all selected landmarks have been attempted; other raw research files are preserved. The one-week response cache remains enabled by default, so a rerun reuses successful landmark responses and retries landmarks whose prior attempts did not produce a cached response.

To retry collection while reusing successful landmark caches, do not pass `--cache-hours 0`:

```powershell
$env:JAUNT_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
python .\backend\scripts\collect_geodata.py --batch-size 1
```

Collect only the four currently unresolved targets while preserving the previously successful building candidates:

```powershell
$env:JAUNT_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
python .\backend\scripts\collect_geodata.py --landmarks 'grand_central_terminal,pershing_square_building,one_vanderbilt,bryant_park'
```

To collect 1 Vanderbilt independently:

```powershell
$env:JAUNT_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
python .\backend\scripts\collect_geodata.py --landmarks 'one_vanderbilt'
```

To retry 1 Vanderbilt through an alternate Overpass instance, replace the placeholder with the chosen instance's interpreter URL. The collector keeps its existing query, aliases, bounding box, geometry output, retries, and cache behavior:

```powershell
$env:JAUNT_OVERPASS_URL = 'https://<alternate-overpass-host>/api/interpreter'
python .\backend\scripts\collect_geodata.py --landmarks 'one_vanderbilt'
```

Use `--cache-hours 0` only when intentionally forcing all batches to be fetched again:

```powershell
python .\backend\scripts\collect_geodata.py --cache-hours 0
```

To request supplemental Nominatim candidates as well (still filtered and unconfirmed):

```powershell
python .\backend\scripts\collect_geodata.py --nominatim --cache-hours 0
```

The script prints a per-landmark candidate count and status, including explicit unresolved summaries. All candidates remain `candidate_unconfirmed` with `verified: false`.

Offline tests (no network requests):

```powershell
python -m unittest discover -s .\backend\scripts -p 'test_collect_geodata.py' -v
python -m unittest discover -s .\backend\scripts -p 'test_collect_nyc_buildings.py' -v
```

Route generation uses the nine `routing_candidate` points in `backend/data/pedestrian_access_point_candidates.json`; it does not change their verification statuses. All candidate points remain unverified and generated routes are labeled `prototype_candidate_unverified`. Do not use this as evidence that the point is an official entrance or that the route is field-verified.

Generate or resume all 72 directed journeys from the repository root:

```powershell
python .\backend\scripts\fetch_routes.py --all
```

The command requests each direct baseline first, then up to two geographically relevant alternatives. It is sequential and defaults to 1.2 seconds between live requests, three attempts, and exponential backoff. Configure with `--delay`, `--retries`, and `--backoff`; valid caches are reused. Use `--force` only to intentionally replace valid cache data. To fetch one pair, use `--origin`, `--destination`, and `--route direct` (or a specific alternative ID).

Candidate alternatives must pass a `≤2.0×` detour cap (`+500 m` maximum extra), `≥50 m` endpoint clearance, `≥20 m` lateral offset from the direct corridor, and `≥100 m` waypoint separation. Newly generated routes that duplicate the direct route or an accepted alternative, or that exceed the actual detour limit (`>750 m` extra or `>2.0×`), are removed from the cache after validation. Their raw request/response evidence is retained and a `__rejected.json` marker records why. Pre-existing route files are never deleted.

GeoJSON routes are stored as `backend/data/routes/{origin_id}__{destination_id}__{route_id}.geojson`. Legacy unscoped Grand Central → Bryant Park files remain supported and journey-scoped files take precedence when both exist. Raw requests, responses, per-route failures, and rejection markers are stored under `backend/data/raw/valhalla/`; schema-2 `route_coverage_report.json` records per-pair status and is updated after each attempt. Before saving or reusing a route, the client validates GeoJSON, LineString coordinates and WGS84 bounds, positive distance/duration, requested journey/route IDs, endpoint proximity, pedestrian costing, and Valhalla provenance. It never uses straight-line fallback geometry.

The API generates a missing baseline on demand and returns 502 if Valhalla or validation fails. Both route and recommendation endpoints read the same cache; changing preferences does not trigger a route request. Existing additional routes are retained, and new alternatives are only requested when their returned geometry is expected to be meaningfully distinct.

## NYC BUILDING footprint candidates

The active landmark set contains nine landmarks, including both `one_vanderbilt` and `stephen_a_schwarzman_building`. The NYC Open Data collector is independent of the OSM collector. It queries the BUILDING dataset (ID `5zhs-2jue`) for polygons intersecting a 200 m circle around each search anchor, requests `the_geom` and available identifying/descriptive fields, and stores raw responses under `backend/data/raw/nyc_open_data/`. Candidate records are written separately to `backend/data/nyc_building_candidates.json`; NYC DOITT_ID/BIN/BBL/OBJECTID values are never presented as OSM IDs. All footprints remain unverified.

NYPL's search anchor is the supplied OSM reference point (40.753014, -73.9817686), used only to find nearby polygons. For 1 Vanderbilt, the collector resolves the stated street address through Nominatim and uses the result only as a spatial search anchor; candidate identity comes from the BUILDING polygon and attributes, not from the geocoder. Missing NAME values do not exclude spatial candidates. Candidate ranking considers proximity, name aliases when present, feature code, and available city identifiers. The retrieved full polygon and original API attributes are preserved.

Collect the targets independently (successful cached responses are reused for seven days; `--cache-hours 0` forces refresh):

```powershell
$env:JAUNT_USER_AGENT = 'JauntRouteRecommender/0.1 (your contact email or project URL)'
python .\backend\scripts\collect_nyc_buildings.py --landmarks 'one_vanderbilt'
python .\backend\scripts\collect_nyc_buildings.py --landmarks 'stephen_a_schwarzman_building'
```

Or request both sequentially in one run:

```powershell
python .\backend\scripts\collect_nyc_buildings.py --landmarks 'one_vanderbilt,stephen_a_schwarzman_building'
```

## Local validation commands

```powershell
python -m py_compile .\backend\scripts\collect_geodata.py .\backend\scripts\fetch_routes.py
python -m json.tool .\backend\data\landmarks.json > $null
```
