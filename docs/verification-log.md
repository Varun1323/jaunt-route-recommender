# Verification and Test Log

This document records implementation checks, geographic data checks, route-generation checks, and automated software tests for the Jaunt prototype.

## 1. Geographic research checks

The project compares OSM feature geometry with NYC Open Data building footprints where both sources provide relevant records. Geometry comparison includes polygon overlap (IoU), boundary-distance measurements, ring validity, and source identifier reconciliation.

The recorded cross-source comparisons support footprint matches for the researched Midtown buildings, including Pershing Square Building, Chrysler Building, Chanin Building, Daily News Building, 1 Vanderbilt, and the Stephen A. Schwarzman Building. Bryant Park is represented as a park feature rather than a building-footprint record.

These checks establish geographic feature correspondence for the research dataset. Building footprint geometry, pedestrian approach points, and route geometry are distinct data concepts and are stored separately.

## 2. Pedestrian network and route checks

The access-point matching workflow projects documented approach locations onto a connected outdoor pedestrian network. The route generator then uses Valhalla pedestrian costing and stores returned geometry as GeoJSON.

Route validation checks:
- GeoJSON structure and LineString coordinate validity.
- WGS84 coordinate bounds.
- Positive finite distance and estimated duration.
- Origin/destination journey identity.
- Endpoint proximity to the journey's routing inputs.
- Pedestrian costing and Valhalla provenance.
- Distinctness and detour limits for alternative routes.

The task-facing application presents eight selectable landmarks, which define up to 56 directed origin/destination journeys (8 origins × 7 destinations). The recorded route-cache snapshot contains 74 valid route records from the earlier broader nine-location collection: 72 baselines and two Grand Central Terminal → Bryant Park alternatives. The API filters route candidates by the selected origin and destination.

All route geometries shown by the prototype are provider-returned pedestrian-network geometries. Walking times are routing-engine estimates.

## 3. Recommendation-engine checks

Automated tests cover:
- Natural-language preference parsing and structured preference validation.
- Positive, negative, and mixed preference expressions.
- Architecture, history, distance, time, and landmark scoring.
- Hard-constraint precedence and time-budget behavior.
- Direct-route retention and candidate comparison.
- Route-generation strategy allowlisting and fallback behavior.
- Cache reuse, idempotency, route validation, and duplicate/detour handling.
- Human-readable explanations based on route metrics and landmark facts.

## 4. API and frontend checks

Backend tests cover the FastAPI endpoints, request validation, route loading, recommendation response shape, error handling, and optional OpenRouter behavior.

Frontend tests cover API integration, preference input, route presentation, and map-related UI behavior. Playwright tests cover browser-level journeys including landmark selection, recommendation changes, map rendering, route alternatives, swapping endpoints, loading, and error handling.

## 5. Recorded automated test results

| Check | Recorded result |
|---|---:|
| Backend test suite (latest local run) | **356 passed, 0 failed, 1 skipped** |
| Frontend unit tests | 6 passed |
| Frontend production build | Passed |
| Playwright end-to-end tests | 6 passed |
| Directed baseline route coverage | 72 / 72 |
| Cached route records | 74 |

The latest local backend test run completed in **54.80 seconds** with **356 passed, 1 skipped, and 1 warning**. The skipped test is the opt-in live Valhalla integration test. The warning is a Pydantic V2 deprecation notice at `backend/scripts/openrouter_client.py:47`: the class-based `Config` style on `RoutingStrategyPayload` should be migrated to `ConfigDict`. It is a deprecation warning, not a test failure. Automated test suites use mocks/fixtures for external provider interactions so the standard test run does not depend on live OpenRouter or Valhalla availability.

## 6. Manual task scenarios

The Jaunt task asks for at least five traveler requests. The following scenario set is the recommended acceptance matrix for demonstrating the behavior:

| Traveler request | Expected decision behavior |
|---|---|
| "I love beautiful old buildings and architecture. I have around 25 minutes." | Favor relevant architecture while keeping the estimated walk within the stated time where a candidate allows it |
| "I need to get there quickly. No unnecessary detours." | Favor the shortest/fastest feasible candidate |
| "I want to see as many interesting buildings as possible." | Favor a route with more relevant intermediate landmarks when the added distance is justified |
| "I prefer history and architecture, but keep the walk short." | Balance place relevance against distance and duration |
| "Include at least one landmark, but do not exceed 10 minutes." | Apply minimum-landmark and maximum-duration constraints before soft scoring |

The backend test suite includes deterministic recommendation and preference scenarios. The screen recording should demonstrate at least five distinct requests in the running UI.

## 7. Geographic interpretation

OSM, NYC Open Data, official landmark sources, and Valhalla each provide different kinds of evidence. The application preserves source provenance and keeps building identity, pedestrian approach locations, and route geometry as separate records.

The prototype is designed to compare pedestrian route candidates using mapped network geometry and routing-engine estimates. It does not provide live crowd, safety, closure, pavement, or accessibility analysis.
