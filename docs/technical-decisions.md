# Technical Decisions

## Planned application stack

React, TypeScript, Tailwind CSS, and Leaflet are the planned map interface. Python/FastAPI is the planned backend, JSON/GeoJSON the data format, and Valhalla the preferred pedestrian-routing engine. No application frontend, API, or recommender has been implemented. No alternatives have been formally benchmarked for those application components.

## Geographic collection

### OSM discovery with Overpass

`collect_geodata.py` uses one-landmark Overpass requests by default because targeted queries are easier to retry and audit than a single broad query. Its endpoint is configurable with `JAUNT_OVERPASS_URL`; it retries transient capacity/timeouts up to three attempts, caches successful responses, and preserves successful candidate records on selective runs. Exact names and aliases are search/ranking evidence only. Failed requests and successful empty results have distinct unresolved states.

### Direct OSM API for known object IDs

Use the direct OSM API for a known way or relation when full member/node geometry is needed, instead of repeating broad Overpass discovery. Direct API requests can return complete way or relation membership and geometry. OSM IDs and raw provider responses remain OSM records. Retained direct way geometries include 1 Vanderbilt (`direct_way_265875648.json`) and Grand Central Terminal (`direct_way_265947358.json`). The full matched response geometries for Pershing Square and Chrysler were analyzed in memory and remain to be archived locally.

### NYC Open Data BUILDING

`collect_nyc_buildings.py` queries NYC Open Data dataset `5zhs-2jue` through its Socrata resource API. It filters `the_geom` by a 200 m `within_circle` search and selects full polygon geometry plus attributes such as `BIN`, `DOITT_ID`, `BASE_BBL`, `OBJECTID`, `NAME`, construction year, feature code, and geometry source. It avoids downloading the citywide million-row dataset. `NAME` is not required for a spatial candidate because the field is incomplete and not actively maintained.

For NYPL the supplied OSM reference point is only the spatial query anchor. For 1 Vanderbilt an address lookup is only the search anchor. Neither search point is substituted for a footprint or entrance. The NYC results and NYC identifiers are stored separately in `nyc_building_candidates.json`; they are never assigned invented OSM IDs.

## Geometry comparison

`geodata_geometry.py` uses local, dependency-free planar calculations suitable for this small Midtown study area. It normalizes OSM way coordinate arrays and GeoJSON polygon/multipolygon exteriors, computes WGS84 bounding boxes, tests polygon overlap, triangulates simple rings for approximate planar IoU, and samples boundaries at roughly one-metre spacing for approximate distance statistics. It also checks OSM way closure/completeness and self-intersection.

These metrics are review evidence, not proof of identity. IoU and boundary distances use a local equirectangular projection; polygon holes are ignored by the current exterior-ring normalization. Keep source geometries separate and preserve the original coordinate order. A building polygon/centroid is not a pedestrian entrance.

## Verification and data preservation

Collection results remain `candidate_unconfirmed`; verification recommendations in research notes are separate from stored `verified: false` fields. Identity should be corroborated with official addresses/designation records, OSM tags and geometry, city identifiers, and cross-source geometry where present. Entrance suitability needs its own evidence. Selective collection should not overwrite prior successful candidate records or raw research evidence.

Provider identifiers and original geometries remain in separate records: OSM type/ID and tags stay with OSM data; NYC `DOITT_ID`, `BIN`, `BASE_BBL`, and `OBJECTID` stay with NYC data. A cross-source match can support a review recommendation without changing either record's verification flag. Current comparisons support same-footprint matches for Pershing Square, Chrysler, NYPL, and 1 Vanderbilt, with attribute conflicts still needing review for NYPL and 1 Vanderbilt. Grand Central Terminal's way and relation are verified as representing the above-ground terminal and underground transit features respectively; the geometry difference between the photogrammetric roof outline and street-level footprint is documented. See [verification-log.md](verification-log.md) for evidence and limits.

## Routing

Valhalla pedestrian costing is selected because it returns pedestrian-network route geometry, distance, and estimated time. `fetch_routes.py` only accepts verified WGS84 inputs with evidence and ordered access waypoints for non-direct routes. It decodes returned polyline6 geometry and does not generate straight-line fallbacks. The public demo is a research endpoint, not a production SLA. No route has yet been retrieved. OpenRouteService is documented as a possible fallback but has not been called or selected.

## Decisions still open

- Review the Grand Central geometry discrepancy and determine which footprint is the intended visible terminal geometry.
- Verify pedestrian entrances/access points separately from landmark footprints.
- Complete systematic checks for Graybar, Chanin, Daily News, and Bryant Park.
- Agree on user preference fields and recommendation behavior after geographic research review.
- Reassess public routing provider policy and availability before using it in an application.
