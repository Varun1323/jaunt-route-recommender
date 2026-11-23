# Project Progress and Handoff

**Updated:** 2026-11-22 (client local date)  
**Current stage:** Final audit and stabilization completed. Full backend test discovery (315 tests, 1 skipped) and frontend test suite (6 tests) pass with 100% offline mocks. Frontend production build succeeded (`tsc && vite build`). All 13 landmarks, the 8-landmark selectable catalogue, 74 baseline/alternative route records, and prototype disclosures are preserved and stabilized.

## Completed

- **Final Audit and Stabilization:**
  - Resolved all legacy test failures/errors in `test_match_access_points.py` and `test_pedestrian_access_points.py`, reconciling test assertions with the 13-landmark dataset and extended Midtown Manhattan study area while maintaining unverified candidate disclosures.
  - Fixed unresolved Pylance imports in `backend/app.py` via clean module aliasing (`backend.scripts.*`), maintaining single runtime module identity without circular imports or structural breaking changes.
  - Verified consistent route-validation thresholds across `fetch_routes.py` and `recommendation_engine.py` (150 m endpoint tolerance, 2.0 max detour ratio, 750 m max extra distance, 35 m / 35% geometric divergence).
  - Verified walking-time budget vs. hard maximum time constraints, DeepSeek waypoint sequence planning, Valhalla candidate generation, direct route retention, and deterministic recommendation engine scoring.
  - Guaranteed automated tests run 100% offline with zero live network calls to Valhalla or OpenRouter.
  - Executed complete full-suite backend tests: **315 passed, 0 failures, 0 errors, 1 skipped (opt-in live test)**.
  - Executed frontend tests (**6 passed, 0 failures**) and verified production bundle build (`dist/` generated with zero errors).
- **Phase 6 — Empty Preference Behaviour & Guaranteed Direct Route Retention:**
  - When query is empty or whitespace, OpenRouter is prompted to plan a sensible default sightseeing walk with balanced priority without inventing arbitrary constraints.
  - The direct route is always retained as candidate 0 for all directed origin-destination journeys.
  - Implemented deterministic local fallback logic for neutral preferences in `_sightseeing_waypoint_sequences`, prioritizing cultural, historic, and architectural landmarks along the geographic origin-destination corridor.
  - OpenRouter timeouts, 5xx errors, or missing API keys fall back gracefully to local preference matching without throwing 500 errors.
  - If sightseeing route generation or Valhalla encounters an error, the endpoint continues and reliably returns the validated direct baseline with transparent generation failure disclosure.
  - Complete backend test suite (201 tests) and frontend test suite (6 tests) pass with 100% offline mocks.
- **Phase 5 — Recommendation Engine for Walking-Time Budgets & Hard Constraints:**
  - Evaluates actual validated candidate routes deterministically.
  - Distinguishes available walking-time budgets (`walking_time_budget_minutes`) from hard maximum time constraints (`max_duration_minutes`).
  - Optimizes sightseeing value against walking-time budgets: rewards relevant landmarks visited within budget, penalizes excessive unused time for 0-landmark routes when budget >= 10 minutes, and never rewards routes simply for being longer.
  - Applies strict feasibility filtering for hard maximum constraints (`max_duration_minutes`): over-limit routes receive `is_feasible = False` and are sorted strictly behind all feasible candidates.
  - When no candidates meet hard constraints, discloses transparently that no feasible candidate was found and returns the closest available alternative with actual duration.
  - Generates transparent natural-language explanations comparing actual route metrics against budgets and constraints; never claims an over-budget route fits the budget.
  - Complete backend test suite (196 tests) and frontend test suite (6 tests) pass with 100% offline mocks.
- Implemented the eight-landmark candidate-route policy: every non-loop recommendation now retains direct route baseline and independently generates a preference/waypoint sightseeing route, with DeepSeek-planned additional sequences bounded to four total candidates; explicit waypoint failures and duplicate geometries are disclosed rather than fabricated.
- Simplified preference interpretation: `backend/scripts/preference_parser.py` now owns the canonical `UserPreferences` schema, structured-payload validation, allowed landmark/category checks, and ordered explicit-waypoint merging. OpenRouter now returns intent only and delegates all provider-payload validation/construction to the canonical parser. Deterministic local parsing remains the fallback for missing or failed provider calls.
- Initialized project instructions, research/source documentation, data directories, and standard-library Python research scripts.
- Added selective OSM/Overpass collection with configurable endpoint, successful-response caching, retries, and separate failure/no-match statuses.
- Added NYC Open Data BUILDING spatial collection for all 9 landmarks, preserving full polygons, source attributes, and separate NYC IDs.
- Added local polygon geometry comparison utilities and offline tests.
- Retrieved and archived full OSM way geometries for all 9 landmarks via the direct OSM API; NYC BUILDING raw geometries retained for 6 landmarks under `backend/data/raw/nyc_open_data/`.
- Resolved reproducibility and regression testing for all 9 landmark cross-source comparisons (IoU, mean and max boundary difference).
- Investigated and resolved attribute discrepancies (NYPL construction year, 1 Vanderbilt BIN/height/date).
- Established pedestrian access-point records for all 9 landmarks (`backend/data/pedestrian_access_points.json`, schema v1.1).
- Retrieved OSM pedestrian network features for the study area (1,343 elements) archived at `backend/data/raw/osm/pedestrian_network_features.json`.
- Implemented and tested pedestrian access-point matching script (`backend/scripts/match_access_points.py`):
  - Projects documented approach frontages onto the connected outdoor OSM pedestrian network (1,419-node main component).
  - Generated candidate routing coordinates for all 9 landmarks (`backend/data/pedestrian_access_point_candidates.json`).
- Refactored `fetch_routes.py` and implemented working pedestrian route generation:
  - Accepts arbitrary origin and destination landmark IDs from the 9 available landmarks.
  - Rejects identical origin and destination selections.
  - Removed requirement for separate manual `route_waypoints.json`; uses candidate coordinates directly with unverified limitation disclosure.
  - Generates genuine pedestrian routes using the Valhalla public demo API; all-pairs batch generates one baseline and preserves existing alternatives.
  - Decodes polyline6 geometry, validates endpoints, distance, and duration.
  - Saves GeoJSON routes with metadata, provenance, and limitation disclosure to `backend/data/routes/`.
- Retrieved and archived 3 genuine pedestrian routes between Grand Central Terminal and Bryant Park:
  - `direct.geojson`: 686.0 m, 494.9 s (~8.2 min)
  - `via_vanderbilt.geojson`: 688.0 m, 507.0 s (~8.4 min)
  - `architecture.geojson`: 1102.0 m, 816.7 s (~13.6 min)
- Built deterministic Recommendation Engine (`backend/scripts/recommendation_engine.py`) and Natural Language Preference Parser (`backend/scripts/preference_parser.py`):
  - Parses free-text queries across 5 dimensions: architecture, shortest distance, minimum walking time, more landmarks, and numeric/qualitative time constraints.
  - Implemented robust negative preference handling recognising common negative constructions ("don't want", "do not prefer", "not interested in", "avoid", "without", "no", "skip"), preserving positive intent and handling mixed statements.
  - Normalizes distance, duration, landmark count, and architectural relevance from actual route GeoJSON and `landmarks.json`.
  - Implemented route-geometry-based landmark detection using candidate coordinates and Haversine point-to-segment distance calculation to identify nearby landmarks along the route walk.
  - Resolved minimum-distance evaluation across all route segments and unified `landmark_count` with `landmarks_visited` (intermediate landmarks excluding origin and destination without duplicates).
  - Applies deterministic penalty functions for routes exceeding time limits when feasible alternatives exist.
  - Generates human-readable explanations comparing route trade-offs against user preferences.
  - Fully documented in `docs/recommendation-engine.md`.
- Implemented Multi-Journey Support across all 72 directed landmark pairs:
  - Supports arbitrary origin and destination selections among all 9 researched landmarks ($9 \times 8 = 72$ directed pairs).
  - Journey-scoped route storage (`{origin_id}__{destination_id}__{route_id}.geojson`) preventing collision across journeys while preserving legacy files.
  - Recommendation engine strictly filters routes by selected `origin_id` and `destination_id` so journeys are never mixed.
  - Handles variable route alternative counts ($N=1, 2, 3$) robustly in normalization and comparative explanations.
- **Generated and independently validated route coverage for all 72 journeys on 2026-11-21:**
  - `python .\backend\scripts\fetch_routes.py --all --delay 1.2 --retries 3` retrieved/reused all 72 baselines with zero failures.
  - Persisted cache has 74 valid route records: 72 direct baselines plus 2 pre-existing Grand Central Terminal → Bryant Park alternatives.
  - One journey (Grand Central Terminal → Bryant Park) has meaningful alternative(s). The distinctness check requires a real distance difference or substantial path separation; older alternatives are preserved.
  - Raw requests/responses are retained per journey and the per-journey resumable report is `backend/data/raw/valhalla/route_coverage_report.json`.
  - Every cached route passes GeoJSON, coordinate, metric, journey-ID, endpoint proximity, pedestrian-costing, and Valhalla provenance validation. All route records retain `prototype_candidate_unverified` status because access points are unverified.
- Built FastAPI Backend Application (`backend/app.py`):
  - `GET /api/landmarks` — returns all 9 researched landmarks with descriptions, categories, and prototype limitation disclosure.
  - `GET /api/routes?origin_id=...&destination_id=...` — returns available genuine walking routes for the requested pair with full GeoJSON FeatureCollection geometry, distance, and duration.
  - `POST /api/recommend` — accepts `origin_id`, `destination_id`, and free-text `preference`, calls `recommend_route()` and returns top recommendation, ranked alternatives, individual scores, estimated walking time, visited landmarks, rationale explanation, and limitation disclosure.
  - Validates landmark IDs with 404 for unknown IDs and 422 for identical origin/destination. A cache miss triggers one shared Valhalla route-generation attempt; provider/validation failures return 502.
  - Uses per-directed-journey re-entrant locks so concurrent API requests do not duplicate baseline or preference-directed route generation.
- Built React, TypeScript, Tailwind CSS, and Leaflet Frontend Application (`frontend/`):
  - Connects to all three FastAPI backend endpoints (`/api/landmarks`, `/api/routes`, `/api/recommend`).
  - Journey selection across all 9 landmarks with swap button and availability indicator.
  - Interactive Leaflet map rendering genuine GeoJSON pedestrian geometries with start/end pins, route highlights, and legend. Never uses straight lines.
  - Free-text traveler preference input with quick suggestion chips and real-time refresh.
  - Full recommendation display with route metrics, visited landmarks, architectural notes, and rationale explanation.
  - Available routes listing with clickable cards to preview alternatives on the map.
  - Prototype coordinate limitation disclosures and clear error/loading state handling.
- Phase 9 OpenRouter AI integration:
  - Added backend-only `backend/.env` with an empty `OPENROUTER_API_KEY` and the requested model/base URL; added matching `backend/.env.example`. The existing `.gitignore` already ignores `.env` and allows `.env.example`.
  - Added a standard-library OpenRouter chat-completions client. Process environment values take precedence over `.env`; the loader does not overwrite or mutate existing environment variables, and API keys are never included in API responses or error messages.
  - Validates model-returned preferences before deterministic route scoring. Missing key and provider failures retain the local parser/scorer and return explicit `ai_status`; after scoring, the model chooses a constrained explanation focus from route facts, and the backend renders the explanation from validated metrics/place metadata (no coordinates/geometries).
  - Added unsupported-preference disclosure for greenery and quietness because no verified route-level evidence supports scoring those qualities.
  - Frontend keeps the existing preference input, map, alternatives, and route metrics, and adds AI/fallback status plus unsupported-preference disclosure.
- Phase 9.1 AI-directed route recommendation:
  - `POST /api/recommend` now reads strict-validated cached route geometry, interprets preferences, computes permissible route-generation strategies, optionally asks OpenRouter to choose only from that backend allowlist, and calls the existing Valhalla fetcher when justified.
  - Added structured history weight, category exclusions, and a bounded maximum detour ratio. Deterministic local parsing remains the fallback and applies the same strategy policy.
  - Route scoring now applies satisfiable time, minimum-landmark, exclusion, and detour constraints before soft preference weights. If no route can satisfy a hard constraint, existing routes remain available and the response explains the unmet condition.
  - Extended `fetch_all_routes()` with bounded `landmark`, `native`, and `both` strategies. Native alternatives can be retrieved without rewriting the cached baseline; route validation, geometry distinctness, actual detour caps, raw evidence, and cache idempotency remain enforced.
  - The API returns route-generation strategy/status, generated IDs, failure counts, and available route count. The frontend refreshes the available route list/map after successful generation; existing map and route selection behavior remains intact.
  - Added offline tests for different preference strategies, route reuse/no unnecessary generation, validated generated-route integration, provider allowlisting/fallback, route-generation errors, hard constraint precedence, and UI route refresh.
- Phase 7 browser QA: added Playwright Test in `frontend/`; tests start the local FastAPI/Vite servers, block external map tiles, and only use cached routes. **5 browser tests passed**, including a UI traversal of all 72 directed pairs, recommendation preference checks, map geometry/markers/bounds, swapping, loading, error cleanup/retry, and the one-route/no-alternatives state.
- Phase 8 multi-route generation support (implementation and offline tests only; **no live Valhalla requests, no new route cache files**):
  - `build_route_definitions()` now fetches the direct baseline first, then selects up to two geographically relevant intermediate landmarks using the direct route's actual geometry.
  - Candidate filters: `≤2.0×` straight-line detour capped by `+500 m`, `≥50 m` endpoint clearance, `≥20 m` lateral offset, `≥100 m` waypoint separation, deterministic score ordering.
  - The two curated Grand Central Terminal → Bryant Park alternatives remain for backward compatibility; all other journeys use the generic selector.
  - Post-fetch rejection removes newly generated duplicates and excessive detours (`>750 m` extra or `>2.0×` direct), writes `__rejected.json` markers with comparison metrics, and retains all raw request/response evidence. Pre-existing cache files are never deleted; duplicates are flagged instead.
  - `routes_are_meaningfully_distinct()` no longer treats a different via-waypoint label as sufficient; it requires an actual distance difference (`≥75 m` / `15%`) or substantial geometry divergence.
  - Batch report schema 2 records valid/alternative/meaningful/rejected/failed route IDs, partial journeys, and the active policy thresholds.
  - Recommendation ranking now sorts by `(is_feasible, final_score)`, so a feasible route cannot be displaced by a higher-scoring infeasible route.
  - `load_routes()` now prefers journey-scoped files over legacy unscoped files when the same `(origin, destination, route_id)` exists.
- Phase 8 backend validation (historical, before Phase 9): **232 tests, 231 passed and 1 opt-in live Valhalla test skipped; 0 failed**.
- Frontend validation before Phase 9: **6 tests passed, 0 failed**; TypeScript checking and Vite production build passed (39 modules transformed).
- Playwright validation before Phase 9: **5 passed, 0 failed**.

## Current source-of-truth state

- `landmarks.json` contains nine active definitions; every latitude/longitude remains null and `coordinate_verification_status` remains `unverified_not_retrieved`.
- All 9 pedestrian access-point candidates in `pedestrian_access_point_candidates.json` are `candidate_unconfirmed`. Their coordinates are projections onto OSM ways and have not been independently verified in the field.
- Routes in `backend/data/routes/` are prototype routes generated using candidate coordinates; their `verification_status` is `prototype_candidate_unverified`.
- Raw Valhalla request and response artifacts are preserved under `backend/data/raw/valhalla/`.

## Generated routes summary (Grand Central Terminal -> Bryant Park)

| Route ID | Route Name | Distance (m) | Est. Duration | Waypoints |
|---|---|---|---|---|
| `direct` | Direct 42nd Street Pedestrian Route | 686.0 | 495 s (8.2 min) | None (direct origin to destination) |
| `via_vanderbilt` | Via One Vanderbilt Transit Hall | 688.0 | 507 s (8.4 min) | 1 Vanderbilt |
| `architecture` | Art Deco Architectural Loop | 1102.0 | 817 s (13.6 min) | Pershing Square Building, Chrysler Building |

All 3 routes use genuine Valhalla pedestrian costing (`costing: "pedestrian"`). No synthetic or straight-line geometry is used.

## Latest test results

Phase 9.1 final offline verification:

- Backend: `python -m unittest discover -s backend\scripts -p "test_*.py"` — **274 run, 273 passed, 1 opt-in live Valhalla test skipped, 0 failed**. The all-72 deterministic recommendation fixture continues to exercise 432 query/journey cases.
- Fetcher regression: `python -m unittest backend.scripts.test_fetch_routes` — **65 passed**; focused cache idempotency regression — **1 passed**.
- Frontend: `npm test` — **6 passed**; `npm run build` (TypeScript check and Vite production build) — **passed**; `npm run test:e2e` — **6 passed**. Playwright blanks the backend key and disables preference-triggered route generation.
- `git diff --check` — **passed**.

The fetcher now avoids a fresh native-alternatives request during an implicit cache-validation call when the direct route is already cached, while an explicitly selected preference strategy can still request alternatives against that cached baseline. A rejected alternative remains skipped on subsequent cache checks. No live Valhalla or OpenRouter request was made during Phase 9.1. The real route cache remains unchanged; route-generation and provider paths are covered offline with fixtures/mocks.

## Phase 7 user journey and QA

1. The frontend loads landmark definitions and defaults to Grand Central Terminal → Bryant Park.
2. Selecting or swapping landmarks requests `GET /api/routes`, then `POST /api/recommend`; the route list, recommendation card, GeoJSON map layer, endpoint markers, and limitation disclosure update from those responses.
3. Preference submission requests a recommendation. When a justified new validated route is generated, the frontend refreshes the route list/map; otherwise it reuses cached routes. Selecting an alternative updates the map selection.
4. Playwright verifies both selectors contain the nine API landmarks, traverses all 72 cached pairings in the real browser, compares recommendation metrics and route geometry, checks map markers/bounds and changed journeys, verifies preference-only reranking, and simulates a route API error to confirm old map layers are removed and retry restores the journey.

No reproducible application bug required a UI/backend fix in this QA pass; initial failing runs were incorrect Playwright locators/helpers and were corrected in the test suite. A one-route journey has zero ranked alternatives by design, not as an error.

## Outstanding work

- **OpenRouter live setup:** The local key is intentionally blank. Enter the existing key into `backend/.env` locally, then manually verify a configured recommendation if desired; never paste or log the key. Automated tests do not require it.
- **Unscored route properties:** Greenery and quietness preferences are surfaced as unsupported until reliable route-level place/environment data is researched and verified.
- **Field/Imagery Verification of Access Points:** The prototype routing coordinates are mathematical projections onto OSM sidewalk ways; they have not been independently confirmed by on-site observation.
- **Live preference-directed generation not yet run:** On-demand behavior has only been tested with mocked fetches. The real cache remains 72 baselines plus 2 legacy alternatives. Actual route variety depends on Valhalla responses and the existing candidate/detour/distinctness policy.
- **Alternative quality depends on live geometry:** Duplicate and excessive-detour rejection is implemented and tested with fixtures, but real Valhalla alternatives still need review after generation.

## Immediate next action

To enable live AI strategy selection locally, enter the existing key in `backend/.env` and start the backend with `uvicorn backend.app:app --reload` from the repository root. A relevant preference may request a bounded on-demand Valhalla alternative; no batch generation was run in this phase. Separately, conduct on-site or high-resolution imagery ground-truthing of the 9 pedestrian access point coordinates when field access is arranged.
