# Project Status and Implementation Summary

**Project:** Jaunt Route Recommender  
**Branch:** `final-working`  
**Purpose:** Submission prototype for the Jaunt preference-aware walking-route task.

## Current implementation

The repository contains a working full-stack prototype:

- React + TypeScript + Vite frontend.
- Leaflet interactive map.
- FastAPI backend with Pydantic validation.
- Natural-language preference parser with optional OpenRouter integration.
- Deterministic recommendation engine.
- Valhalla pedestrian route generation and GeoJSON route cache.
- Geographic research catalogue, source records, and collection utilities.
- Backend unit tests, frontend tests, production build, and Playwright browser tests.

## Task requirements mapped to implementation

| Jaunt task part | Project artefact / implementation |
|---|---|
| Part 1 — Research | `docs/research.md`, `docs/data-sources.md`, landmark catalogue, OSM and NYC Open Data source archives |
| Part 2 — Solution design | `docs/project-overview.md`, `docs/technical-decisions.md`, `docs/recommendation-engine.md`, `docs/route-methodology.md` |
| Part 3 — Working prototype | `frontend/`, `backend/app.py`, `backend/scripts/`, `backend/data/routes/` |
| Part 4 — Challenge and test | Backend tests, frontend tests, Playwright tests, recommendation scenarios and regression tests |
| Part 5 — Learning and submission | Screen recording, one-page explanation, AI-use examples, unsuitable AI answer example, and next-week improvement plan (submission materials) |

## Main user journey

1. Select an origin and destination.
2. Enter a natural-language preference or choose a suggested example.
3. Request a recommendation.
4. The backend interprets the preference and evaluates route candidates.
5. The interface displays the recommended route, route alternatives, estimated time, distance, intermediate landmarks, and explanation.
6. Select an alternative to inspect its geometry on the map.

## Geographic and route data

The research catalogue includes nine core Midtown landmarks. The broader application catalogue includes additional visitor-interest locations. Route data is stored as GeoJSON and generated with Valhalla pedestrian costing. The route cache includes 72 directed baseline journeys across the nine core research landmarks, plus two existing alternatives for Grand Central Terminal → Bryant Park (74 route records in the recorded project snapshot).

The route generator supports bounded waypoint and native-alternative strategies. It preserves raw Valhalla request/response evidence and validates geometry, route metrics, journey identity, endpoint proximity, provenance, and meaningful route distinctness.

## Recommendation behavior

The parser supports architecture, history, shortest distance, minimum walking time, landmark interest, time budgets/limits, detour bounds, minimum landmark requirements, and category exclusions.

The scorer evaluates hard constraints before soft preference weights. It balances route length and estimated duration against relevant intermediate places. The direct route remains a baseline candidate, and the explanation compares the recommendation with available alternatives.

OpenRouter is optional. When configured, it assists with structured preference interpretation and constrained strategy/explanation steps. Local parsing and deterministic scoring provide the fallback path.

## Recorded automated verification

The project verification record includes:

- Backend offline test suite: **315 passed, 0 failed, 1 skipped** (opt-in live Valhalla test).
- Frontend unit tests: **6 passed**.
- Frontend production build: **passed**.
- Playwright end-to-end tests: **6 passed**.
- Route coverage: **72 of 72 directed baseline journeys** in the nine-landmark research set.
- Route cache: **74 route records** in the recorded snapshot.

These are the results recorded during project development. To reproduce the checks in a local checkout, use the commands in the root README and the frontend package scripts.

## Submission checklist from the Jaunt task

- [ ] Working prototype link or runnable code with setup instructions.
- [ ] Short screen recording showing different traveler preferences and map recommendations.
- [ ] One-page explanation of research, solution approach, and design decisions.
- [ ] Three examples of AI assistance during development.
- [ ] One example of an incorrect or unsuitable AI answer and how it was checked or corrected.
- [ ] One improvement that would be prioritized with another week.

## Suggested screen-recording sequence

1. Introduce the task: preference-aware walking routes rather than fastest-route-only.
2. Select Grand Central Terminal → Bryant Park.
3. Enter an architecture-focused request with a short time budget.
4. Show the recommended route, intermediate landmark, map geometry, distance, time, and explanation.
5. Change the request to a fastest/shortest walk and show the recommendation change.
6. Change the request to prioritize more architectural landmarks and show the longer route.
7. Briefly explain the route-scoring trade-off and the role of AI versus deterministic backend logic.
