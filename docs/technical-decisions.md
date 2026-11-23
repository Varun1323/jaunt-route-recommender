# Technical Decisions and Rationale

## Product decisions

### Preference-aware routes rather than fastest-route-only

The task asks for a recommendation that changes with traveler priorities. The prototype therefore compares route candidates against structured preferences rather than returning a single fastest route for every request.

### Natural-language input

Travelers can describe their goals in ordinary language. The parser maps that input to supported dimensions and constraints. A deterministic local parser keeps the main recommendation workflow usable without an external AI credential.

### Deterministic route ranking

AI is used as an interpretation aid, not as the authority for route facts or ranking. The backend validates structured preferences and applies deterministic scoring to actual route metrics and landmark metadata. This makes the recommendation reproducible and testable.

## Technology decisions

| Choice | Rationale |
|---|---|
| React + TypeScript | Component-based interface with typed API contracts |
| Vite | Lightweight frontend development and production build |
| Tailwind CSS | Consistent styling with a small implementation footprint |
| Leaflet | Interactive map with direct GeoJSON layer support |
| FastAPI | Python API framework with request validation and interactive API documentation |
| Pydantic | Structured input and output validation |
| Valhalla | Pedestrian route geometry, distance, and estimated duration |
| OpenStreetMap | Open geographic features and pedestrian network data |
| NYC Open Data | Municipal building-footprint polygons and identifiers |
| OpenRouter | Optional natural-language preference interpretation |
| JSON / GeoJSON | Human-readable catalogue, cache, route, and provenance formats |

## Geographic data decisions

- Official institutional and preservation sources are used for descriptions and historical context.
- OSM and NYC Open Data records retain separate identifiers and provenance.
- Building polygons are used to understand building footprints; they are not substituted for pedestrian entrances.
- Route geometry comes from Valhalla's pedestrian network response.
- The route cache stores source metadata and raw request/response evidence.

## Route-generation decisions

- Retain a direct pedestrian route as the baseline.
- Generate waypoint alternatives only when a place offers a meaningful geographic and preference-related benefit.
- Bound detours and reject duplicate geometries.
- Preserve existing valid routes and raw provider evidence.
- Keep walking time separate from sightseeing dwell time.

## Recommendation decisions

The recommendation engine considers:

- Walking distance and estimated duration.
- Architecture and history relevance.
- Intermediate landmark count and identity.
- Explicit minimum-landmark requirements.
- Maximum duration and detour constraints.
- Excluded categories and negative preferences.

Hard constraints are considered before soft preference weights. If a hard constraint cannot be met by any available route, the API explains the trade-off and returns the closest available candidate rather than inventing a compliant route.

More landmarks are not automatically better. Landmark value is balanced against distance and time, and the route must be meaningfully aligned with the request.

## AI integration decisions

- OpenRouter is optional and configured only on the backend.
- The model returns structured intent rather than route geometry.
- Model output is validated against the application's preference schema and allowed values.
- Route-generation strategy choices are restricted to a backend-computed allowlist.
- The deterministic scoring engine selects the recommendation.
- Explanation text is assembled from validated route metrics and known landmark metadata.
- Missing credentials, provider errors, and timeouts use the local parser/scoring path.

## Scope boundaries

The current prototype evaluates supported place and route attributes. It does not model live crowd density, perceived quietness, greenery quality, real-time closures, pavement condition, or accessibility. Those qualities require suitable data and are not inferred from unsupported signals.
