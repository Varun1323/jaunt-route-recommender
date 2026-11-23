# Jaunt Route Recommender — Agent Instructions

## Objective

Maintain Jaunt, a walking-route recommendation application for nine Manhattan landmarks. It compares genuine Valhalla pedestrian routes against traveler preferences. Preserve the separation between route retrieval, API, frontend, and recommendation engine.

## Current stack and architecture

- Frontend: React, TypeScript, Tailwind CSS, Leaflet (`frontend/`).
- Backend API: Python, FastAPI (`backend/app.py`).
- Research and route utilities: Python scripts in `backend/scripts/`.
- Data: JSON and GeoJSON under `backend/data/`; documentation under `docs/`.
- Routing: Valhalla pedestrian costing with retry, throttling, route validation, raw response retention, a journey-scoped cache, and a resumable all-pairs batch command.
- The recommendation engine remains independent of UI code; preference changes rerank cached routes and must not trigger route retrieval.

## Geographic data rules

- Coordinates use WGS84. GeoJSON position order is `[longitude, latitude]`.
- Preserve provider records, identifiers, geometries, source URLs, retrieval metadata, and verification status. Keep OSM and NYC identifiers and geometries in their separate source records.
- A building polygon or centroid is not a public entrance. Store verified pedestrian access points separately and only with evidence.
- Candidate matches stay unverified until identity is corroborated. A matching name or API response alone is not verification.
- Do not fabricate names, addresses, identifiers, source URLs, coordinates, footprints, route geometry, distances, durations, or access information. Use `null`, `candidate_unconfirmed`, or an explicit unresolved status when evidence is missing.
- Never use straight-line geometry as a walking route. Route geometries must come from an actual pedestrian-routing response.
- Preserve raw responses; do not overwrite earlier research evidence or successful candidate records during selective collection.
- Respect OSM attribution/ODbL, Nominatim limits, NYC Open Data terms, and hosted routing-service policies.

## Working rules for every coding agent

1. Read this file, the root README, relevant docs, code, tests, and data structures before editing.
2. Inspect the actual repository state; do not assume earlier chat summaries describe current files.
3. Keep changes within the requested scope and preserve existing architecture and data.
4. Run relevant tests after implementation changes. Do not make live geographic requests as a test substitute; mock network access in offline tests.
5. Update `docs/progress.md` after every significant milestone.
6. Update relevant technical documentation when implementation or decisions change.
7. Record changed files and tests executed, and distinguish tests actually run from historical test results.
8. Keep `docs/verification-log.md` synchronized with evidence. Distinguish review recommendations from statuses stored in data files.
9. Record unresolved problems and the immediate next action.
10. Before any agent handoff, update documentation and ensure it accurately describes what is and is not complete.
11. Never claim unverified work is complete. Do not commit or push unless explicitly requested.

## Current milestone

All 72 directed origin-destination journeys have validated Valhalla pedestrian baseline routes in `backend/data/routes/`; two pre-existing alternatives remain for Grand Central Terminal → Bryant Park (74 valid route records total). Route generation is not equivalent to access verification: all nine candidate endpoints remain `candidate_unconfirmed`, and route metadata is `prototype_candidate_unverified`. See [project progress](docs/progress.md), [route methodology](docs/route-methodology.md), and the [verification log](docs/verification-log.md). Run `python .\backend\scripts\fetch_routes.py --all` to validate/reuse coverage and resume any missing pairs.
