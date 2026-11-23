# Project Overview

## Assignment and product objective

Jaunt is a planned walking-route recommendation application for Manhattan. The initial trip is from **Grand Central Terminal** to **Bryant Park**. The eventual product will compare three pedestrian routes:

1. A direct pedestrian-network route.
2. An architecture-focused route with verified, walkable intermediate viewing points.
3. An alternative-street route offering a different walking experience.

The application is intended to evaluate route candidates against traveler preferences. Specific preference inputs, weights, route scoring, and recommendation behavior remain to be designed after the geographic research is reviewed.

The active landmark set has nine entries: Grand Central Terminal, Pershing Square Building, Chrysler Building, Graybar Building, Chanin Building, Daily News Building, 1 Vanderbilt, the Stephen A. Schwarzman Building (NYPL), and Bryant Park.

## Planned technology stack

- **Frontend:** React, TypeScript, Tailwind CSS, Leaflet.
- **Backend:** Python and FastAPI.
- **Data:** JSON and GeoJSON.
- **Pedestrian routing:** Valhalla, subject to availability, policy, and coverage verification.

## Current implementation

There is no frontend, FastAPI service, or recommendation engine. The current Python scripts are research and collection utilities:

- `backend/scripts/collect_geodata.py` collects OSM feature candidates through Overpass, with selective landmark collection, caching, retries, and unverified candidate output.
- `backend/scripts/collect_nyc_buildings.py` collects nearby full building-footprint polygons from the NYC Open Data BUILDING dataset for 1 Vanderbilt and NYPL.
- `backend/scripts/geodata_geometry.py` compares local polygon bounds, overlap, IoU, boundary distances, and OSM ring validity.
- `backend/scripts/fetch_routes.py` requests pedestrian routes from Valhalla and writes only provider-returned route geometry. It refuses unverified points and missing waypoint inputs.

## Data layout and conventions

- `backend/data/landmarks.json`: nine active landmark definitions. Coordinates are still null and marked unverified.
- `backend/data/landmark_candidates_v2.json`: OSM candidate report. The file retains provider-level candidate statuses; candidate responses do not approve identity or entrances.
- `backend/data/nyc_building_candidates.json`: NYC BUILDING candidates for NYPL and 1 Vanderbilt. NYC IDs and polygons stay separate from OSM records.
- `backend/data/raw/osm/` and `backend/data/raw/nyc_open_data/`: raw collection/cache evidence, including a direct OSM geometry file for way `265875648`.
- `backend/data/routes/`: currently only `.gitkeep`; no route result is available.
- `docs/`: research, source policy, route methodology, decisions, progress, and verification evidence.

Coordinates are WGS84. GeoJSON positions are `[longitude, latitude]`. Building representative points and geocoder points are not entrance points. Route GeoJSON must be actual pedestrian-engine output, not a straight line.

## Expected future functionality

After geographic inputs and public access points are reviewed, the application may display route alternatives on a Leaflet map, show distance and estimated walking time from the routing provider, and compare traveler-selected preferences. Sightseeing dwell time must remain separate from walking time. The recommendation engine must stay independent from presentation code.

## Current limitations

- No origin/destination coordinates are approved for routing and no public entrance points are verified.
- No genuine Valhalla pedestrian route geometry, distance, or duration is stored.
- Five landmarks have recent review recommendations, but all underlying candidate records remain `candidate_unconfirmed` and all `landmarks.json` coordinates remain unverified.
- Four landmarks still await systematic verification. Details and evidence retention limits are in [verification-log.md](verification-log.md).
