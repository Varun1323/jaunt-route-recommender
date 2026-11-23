# Jaunt Route Recommender — Project Overview

## The task

This project was built for the Jaunt technical task: design and implement a small web prototype that recommends a walking route based on what a traveler wants from the journey.

A conventional map application generally optimizes for getting from A to B. Jaunt explores a different question: **which walking route best matches this traveler's priorities?** One traveler may value architecture and history, another may want the shortest walk, and another may want a useful balance of sightseeing and travel time.

The prototype focuses on Midtown Manhattan and supports selectable start and end landmarks, natural-language preferences, pedestrian route candidates, map visualization, route metrics, and an explanation of the recommendation.

## How the application works

1. The traveler selects an origin and destination.
2. The traveler describes their preference in natural language.
3. The React application sends the journey and preference to the FastAPI backend.
4. The backend interprets the preference using OpenRouter when configured, with a deterministic local parser as a fallback.
5. The backend loads validated pedestrian route candidates and can request additional candidates from Valhalla when the selected strategy warrants it.
6. The recommendation engine evaluates route distance, estimated walking time, intermediate landmarks, architectural and historical relevance, and applicable constraints.
7. The API returns the recommended route, ranked alternatives, metrics, and a human-readable explanation.
8. The frontend displays the route geometry and supporting information on an interactive Leaflet map.

The route ranking is performed by deterministic backend logic. AI assists with interpreting natural-language intent and selecting from bounded backend options; it does not invent route geometry or choose a winner outside the scoring engine.

## Study area and landmark catalogue

The research catalogue contains nine core landmarks used for the route study:

- Grand Central Terminal
- Pershing Square Building
- Chrysler Building
- Graybar Building
- Chanin Building
- Daily News Building
- 1 Vanderbilt
- Stephen A. Schwarzman Building (New York Public Library)
- Bryant Park

The application also includes the Midtown visitor-interest catalogue used by the current UI, including Times Square, Rockefeller Center, St. Patrick's Cathedral, and the Museum of Modern Art. The backend data and frontend selection catalogue are maintained separately to support the broader research set and the task's user-facing journeys.

## Technology

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | React, TypeScript, Vite | User input, journey selection, recommendation and route presentation |
| Map | Leaflet | Interactive map and pedestrian GeoJSON rendering |
| Backend | Python, FastAPI, Pydantic | API, validation, preference interpretation, route orchestration |
| Preference AI | OpenRouter API | Optional structured interpretation and explanation focus |
| Routing | Valhalla pedestrian costing | Road/path-network geometry, distance, estimated duration |
| Geographic data | OpenStreetMap, NYC Open Data, official landmark sources | Place context, mapped geometry, building footprints and provenance |
| Storage | JSON and GeoJSON files | Landmark catalogue, route cache, raw source responses and metadata |

## Main API

- `GET /api/landmarks` — returns the landmark catalogue.
- `GET /api/routes?origin_id=...&destination_id=...` — returns route candidates for a selected journey.
- `POST /api/recommend` — interprets a traveler preference, evaluates route candidates, and returns a recommendation with alternatives and rationale.

The repository also contains a separate `/api/v1/recommend` workflow. The current frontend recommendation flow uses `POST /api/recommend`.

## Scope

Jaunt is a task prototype for preference-aware walking-route recommendation. It demonstrates research-led place selection, real pedestrian-network route geometry, route comparison, deterministic scoring, natural-language preference interpretation, and map-based presentation.

Walking durations are routing-engine estimates. The prototype does not model live crowd density, real-time closures, pavement condition, or accessibility status. It is not intended to replace a navigation service.
