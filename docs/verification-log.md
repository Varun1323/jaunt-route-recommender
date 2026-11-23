# Landmark Verification Log

**Updated:** 2026-11-21 (client local date)  
**Scope:** Reproducible cross-source geometry evidence for all 9 landmarks. All raw geometry files are archived and regression-tested. Statuses below are human review recommendations; all stored candidate fields remain `verified: false`.

| Landmark | Review status | Evidence and remaining issue |
|---|---|---|
| Grand Central Terminal | **NEEDS_REVIEW** | OSM way `265947358` (BIN `1035381`) archived in `direct_way_265947358.json` (39 nodes). NYC BUILDING DOITT `49507`, BIN `1035381`, BBL `1012800001` archived in `building_grand_central_terminal.json`. IoU `0.624459`; mean boundary difference `12.03 m`; maximum `24.99 m`. Discrepancy is concentrated along the 42nd St south facade (~24 m offset): NYC DOITT `49507` uses `geom_source: Photogrammetric` (11 vertices, roofline capture); OSM way `265947358` (39 vertices) captures the street-level building envelope including south entrance portals. The viaduct/shadow hypothesis for the photogrammetric offset is unconfirmed; treat as a hypothesis only. Recommendation: use OSM way `265947358` as the above-ground footprint; retain NYC DOITT `49507` for municipal identifier linkage. Official address: 89 E 42nd St. Pedestrian access-point candidate on OSM way `1111692728` (footway, sidewalk) at 28.73 m projection distance — medium confidence; manual review required. |
| Pershing Square Building | **VERIFIED** | OSM way `265947370` ↔ NYC DOITT `563691`, BIN `1036151`, BBL `1012960001`. Archived in `direct_way_265947370.json` and `building_pershing_square_building.json`. IoU `0.945406`; mean `0.66 m`; max `1.00 m`. Regression test passes. VERIFIED applies to building footprint identity only; entrance coordinates are not verified. Official address: 125 Park Ave (exterior viewing and subway access; not general interior access). |
| Chrysler Building | **VERIFIED** | OSM way `42500770` ↔ NYC DOITT `464793`, BIN `1036156`, BBL `1012970023`. Archived in `direct_way_42500770.json` and `building_chrysler_building.json`. IoU `0.870962`; mean `2.05 m`; max `4.18 m`. Regression test passes. VERIFIED applies to footprint identity only; exterior publicly viewable; interior access not established. Official address: 405 Lexington Ave. |
| Graybar Building | **VERIFIED** | OSM way `265947361` ↔ NYC DOITT `231162`, BIN `1035385`, BBL `1012780001`. Archived in `direct_way_265947361.json` and `building_graybar_building.json`. IoU `0.967821`; mean `0.65 m`; max `1.14 m`. Regression test passes. VERIFIED applies to footprint identity only. Official address: 420 Lexington Ave. |
| Chanin Building | **VERIFIED** | OSM way `265947366` ↔ NYC DOITT `534814`, BIN `1036152`, BBL `1012960037`. Archived in `direct_way_265947366.json` and `building_chanin_building.json`. IoU `0.936971`; mean `0.61 m`; max `0.93 m`. Regression test passes. VERIFIED applies to footprint identity only. Official address: 122 E 42nd St. |
| Daily News Building | **VERIFIED** | OSM way `42500778` ↔ NYC DOITT `800556`. Archived in `direct_way_42500778.json` and `building_daily_news_building.json`. IoU `0.939474`; mean `1.13 m`; max `2.16 m`. Regression test passes. VERIFIED applies to footprint identity only. Official address: 220 E 42nd St. |
| Bryant Park | **VERIFIED** | OSM way `22727025` archived in `direct_way_22727025.json` (9 nodes, complete valid ring, ~23,404 m², ~5.78 acres). Park boundary verified against official park footprint; no NYC BUILDING counterpart applicable (open public space). Regression test passes. Public park; general access is unrestricted during operating hours. Official park information: [bryantpark.org](https://bryantpark.org/). |
| 1 Vanderbilt | **VERIFIED** | OSM way `265875648` ↔ NYC DOITT `1284912`, BIN `1090825` (active DOB), BBL `1012770027`. Archived in `direct_way_265875648.json` (utf-8-sig BOM). IoU `0.956879`; mean `0.71 m`; max `3.54 m`. Regression test passes. **Attribute resolutions:** OSM BIN `1035349` is obsolete (demolished 317 Madison Ave building); correct active BIN is `1090825`. OSM height 397 m = roof/parapet height; NYC 1,401 ft = architectural spire tip — both correct, different measurements. Opening: 2020 (tower) / 2021 (SUMMIT observatory + DOB Certificate of Occupancy). VERIFIED applies to footprint identity; SUMMIT access requires ticket purchase. Official address: 45 E 42nd St. |
| Stephen A. Schwarzman Building (NYPL) | **VERIFIED** | OSM way `265302076` ↔ NYC DOITT `556382`, BIN `1034194`, BBL `1012570001`. Archived in `direct_way_265302076.json` (or in OSM collector cache). IoU `0.933697`; mean `1.59 m`; max `4.46 m`. Regression test passes. **Attribute resolution:** NYC construction year `1940` is an administrative tax-lot artifact; correct date is 1911-05-23 (opened May 23, 1911). VERIFIED applies to footprint identity; OSM false node attribution (`1634448982`) removed from access-point record. Official accessible entrance: 10 West 42nd St. |

## Pedestrian access points

Records in `backend/data/pedestrian_access_points.json` (schema v1.1):
- All 9 landmarks: `access_description_status: "verified"`, `coordinate_verification_status: "needs_review"`, `status: "needs_review"`.
- Access descriptions, street addresses, and official source URLs are verified against authoritative sources.
- GPS coordinates are **not** independently verified; do not use as routing origins/destinations without field or imagery confirmation.

Projected candidate routing coordinates are in `backend/data/pedestrian_access_point_candidates.json`:
- All 9: `verification_status: "candidate_unconfirmed"`, `manual_review_required: true`.
- All 9 candidates are on the main connected pedestrian component (1,419 nodes); all use surface outdoor ways (`tunnel=yes` and negative-layer ways excluded).
- These candidate coordinates serve as prototype inputs for route generation (`fetch_routes.py`), with explicit limitation disclosure attached to each generated route.

## Route generation status

Pedestrian route generation is active via Valhalla public demo (`fetch_routes.py`):
- Batch command `python .\backend\scripts\fetch_routes.py --all --delay 1.2 --retries 3` generated/resumed and validated **72/72 directed baseline journeys**, with zero failed journeys.
- The persisted cache contains **74 distinct valid routes**: one baseline per pair plus two pre-existing Grand Central Terminal → Bryant Park alternatives. `route_coverage_report.json` reports one journey with meaningful alternatives (materially different geometry or distance). The report uses status `route_generated_candidate_unverified`, explicitly separating route validation from access-point verification.
- Multi-route generation is implemented and offline-tested, but **no new live alternatives have been fetched**. Current coverage therefore remains 72 baselines plus 2 legacy alternatives. Post-generation verification must review the schema-2 report, `__rejected.json` markers, and each accepted alternative's geometry before treating it as useful.
- Every route uses genuine Valhalla pedestrian costing (`costing: "pedestrian"`); geometry is decoded from Valhalla polyline6 responses. No straight-line geometry is used. Raw request/response evidence is retained in `backend/data/raw/valhalla/`.
- Cache validation checks GeoJSON FeatureCollection/Feature structure, LineString coordinates and WGS84 bounds, positive finite distance/duration, route/journey IDs, current candidate endpoint proximity, pedestrian costing, and Valhalla provenance.
- Route metadata continues to use `verification_status: "prototype_candidate_unverified"`: the Valhalla geometry is genuine, but the access coordinates are not independently field-verified. A valid generated route must not be interpreted as a verified public entrance or safe/accessibility-certified journey.

## Pedestrian network exclusion and inclusion rules (implemented)

The `is_walkable_way()` function in `match_access_points.py` enforces:

**Dedicated pedestrian infrastructure (included unconditionally if outdoor/surface):**
- `highway` in `{"footway", "pedestrian", "steps", "path"}`

**Road carriageway handling:**
- Ordinary roads (`primary`, `secondary`, `tertiary`, `residential`, `unclassified`, `living_street`) are permitted **only** when they carry an integrated sidewalk mapped directly on the carriageway (`sidewalk` in `{"yes", "both", "left", "right"}`).
- Roads with `sidewalk=no`, `sidewalk=none`, `sidewalk=separate` (where sidewalks are mapped as separate footways), or missing `sidewalk` tags are strictly **excluded** from the pedestrian routing graph.

**Outdoor/surface and access exclusions (all must pass):**
- `tunnel=yes` ways are excluded
- Ways with negative `layer` (e.g. `layer=-1`, `layer=-3`) are excluded
- `indoor=yes` ways are excluded
- `railway` and `subway=yes` ways are excluded
- `public_transport=platform` ways are excluded
- `area=yes` ways (plazas) are excluded from linear edge graphs
- `access=private/no/customers/delivery/emergency` ways are excluded
- `foot=no/private` ways are excluded
- Ways with `highway` in `{"service", "motorway", "trunk", "track", "construction", "proposed"}` or missing `highway` tags are excluded

## Graph validation and largest component identification

Candidates are validated against `find_main_pedestrian_component()`, which identifies the largest connected component (1,419 nodes in the study area). Isolated subgraphs or disconnected components are not treated as the main pedestrian network regardless of their node count. Candidate projections require at least one segment endpoint to belong to this verified component. Reference coordinates and candidate projections are explicitly flagged as unverified.

## Interpretation and limits

`VERIFIED`, `NEEDS_REVIEW`, and `UNVERIFIED` above are human review recommendations, not stored candidate approvals. `VERIFIED` concerns building footprint identity and cross-source geometry agreement only. It does not verify pedestrian entrance coordinates, interior access, or routing suitability. Routes have been generated for research/prototype use with explicitly unverified candidate points; independently confirm access points before treating the journeys as field-verified or using them for production navigation.

## Phase 9 OpenRouter integration verification

These application-integration results do not change any geographic identity, access-point, or route verification status above.

- Backend configuration: `backend/.env` contains the requested OpenRouter model/base URL and an empty API key; `backend/.env.example` mirrors it. `git check-ignore backend/.env` confirmed it is ignored. The root ignore rules already ignore `.env` and exempt `.env.example`.
- Provider contract: automated tests mock `urllib` responses and verify request configuration, preference schema validation, safe missing-key behavior, HTTP/network errors, timeouts, and that explanation context contains supplied route facts without coordinates/geometry.
- API/UI behavior: successful mocked provider use, deterministic fallback on parse/explanation errors, explicit status, unsupported greenery/quietness disclosure, and existing cached-route/map/browser flows are tested. Playwright explicitly blanks `OPENROUTER_API_KEY` for its backend process so the browser suite cannot make live provider calls.
- Executed for Phase 9: backend discovery **263 run, 262 passed, 1 opt-in live Valhalla test skipped**; frontend `npm test` **6 passed**; `npm run build` **passed**; `npm run test:e2e` **5 passed**; `git diff --check` **passed**.
- No live OpenRouter request was made and no API key was entered. The enabled AI provider path therefore remains unverified against the live service/model; enter a local key and manually exercise it after review.
- Immediate next action: review the implementation, then enter the key only in `backend/.env` if live AI verification is wanted. Route access points remain candidate/unverified as described above.

## Phase 9.1 preference-directed route workflow verification

These application tests do not alter any geographic verification statuses, and no existing route data was regenerated or modified.

- The API reuses the structured preference parser, bounded strategy selection, OpenRouter allowlist, deterministic scorer, and existing Valhalla fetcher. Provider failure or missing credentials retain deterministic fallback behavior.
- Fetcher cache behavior: an implicit cache check does not issue a native-alternatives request for a cached baseline; an explicitly requested strategy may request bounded alternatives while preserving the cached baseline. Previously rejected alternatives remain skipped. Focused idempotency regression: **1 passed**; fetcher test module: **65 passed**.
- Offline backend suite: `python -m unittest discover -s backend\scripts -p "test_*.py"` — **274 run, 273 passed, 1 opt-in live Valhalla test skipped, 0 failed**.
- Frontend suite: `npm test` — **6 passed**; `npm run build` (TypeScript check and production build) — **passed**; `npm run test:e2e` — **6 passed**. E2E configuration blanks the OpenRouter key and disables preference-triggered route generation.
- `git diff --check` — **passed**. No live OpenRouter or Valhalla requests were made; the route cache remains unchanged.
- Remaining limitations are unchanged: live OpenRouter/model and preference-directed Valhalla alternatives have not been manually verified; all route endpoint coordinates remain prototype/unverified; unsupported greenery and quietness preferences cannot be scored from current verified route-level data.
