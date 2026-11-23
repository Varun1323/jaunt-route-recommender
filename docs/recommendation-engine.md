# Recommendation Engine

**Implementation:** `backend/scripts/preference_parser.py`, `backend/scripts/recommendation_engine.py`, `backend/scripts/openrouter_client.py`

## 1. Purpose

The recommendation engine ranks available pedestrian routes according to a traveler's natural-language request. It compares actual route distance, estimated duration, intermediate landmarks, and place relevance. It uses the same scoring and constraint logic across requests rather than returning a hardcoded route for each example.

The engine is deterministic once the route candidates and structured preferences are supplied.

## 2. System flow

```text
Traveler request
      |
      v
OpenRouter interpretation (optional)
      |
      v
Preference parser and schema validation
      |
      v
Validated route candidates + landmark metadata
      |
      v
Hard-constraint evaluation
      |
      v
Weighted route scoring and ranking
      |
      v
Recommendation, alternatives, metrics, explanation
```

If OpenRouter is unavailable or no API key is configured, the local preference parser supplies the structured preference object. The backend validates all structured preferences before using them.

## 3. Supported preference dimensions

| Dimension | Meaning | Example |
|---|---|---|
| Architecture | Interest in notable architectural styles and buildings | "I love Art Deco buildings" |
| History | Interest in historic places and heritage | "I want to see historic places" |
| Shortest distance | Prioritize a shorter route | "Take the shortest walk" |
| Minimum walking time | Prioritize a quicker journey | "I need to get there quickly" |
| More landmarks | Value additional relevant intermediate places | "Show me interesting buildings along the way" |
| Minimum landmarks | Require a minimum number of intermediate landmarks | "Include at least one landmark" |
| Maximum duration | Set a walking-time ceiling | "I have around 15 minutes" |
| Maximum detour ratio | Bound additional distance relative to the direct route | "Avoid a big detour" |
| Excluded categories | Exclude categories or place types | "Skip museums" |

The parser also handles negative and mixed preferences, such as "I like architecture, but I don't want a long detour."

## 4. Route facts used for scoring

Each route is evaluated from its validated GeoJSON and metadata:

- Walking distance in metres.
- Estimated walking duration in seconds/minutes.
- Intermediate landmarks detected along the route.
- Landmark categories and architectural/historical relevance.
- Extra distance and time compared with the direct/shortest available option.
- Whether the route satisfies explicit constraints.

Origin and destination landmarks are not counted as intermediate sightseeing stops. Landmark detection uses route geometry and geographic distance calculations rather than simply counting labels attached to a route.

## 5. Constraint and scoring approach

The engine separates hard constraints from soft preferences.

### Hard constraints

Examples include maximum duration, maximum detour ratio, minimum landmark count, and excluded categories. When at least one candidate satisfies a hard constraint, candidates that violate it are ranked behind candidates that satisfy it. If no candidate satisfies a constraint, the available routes remain rankable and the explanation identifies the trade-off.

### Soft preferences

Routes are normalized against the available candidate set for distance, duration, architecture, history, and landmark value. The preference weights determine the relative contribution of those dimensions to the final score.

A route with more landmarks does not automatically win. Its additional sightseeing value must justify the added walking distance and time, and it must remain compatible with the traveler's constraints.

### Walking-time budgets

A stated time budget is interpreted separately from a strict maximum. A budget expresses how much time the traveler would like to spend; a hard maximum is a constraint. The engine can reward useful sightseeing within a budget without treating a route over a strict maximum as feasible.

## 6. Route-generation strategy

The recommendation API can decide whether the existing cache contains enough candidates for the request. If another candidate is justified, the backend selects a bounded strategy from its own allowlist. OpenRouter may select only among those permitted options; the local policy provides the fallback.

The supported strategies include landmark-based waypoint generation and native Valhalla alternatives. Existing valid baselines are retained, generated routes are validated before scoring, and duplicate or excessive-detour geometries are filtered.

## 7. Explanation generation

The response explains the winning factors using backend-validated facts, including:

- Why the route matches the traveler's priorities.
- Its estimated distance and walking time.
- Which intermediate landmarks it visits.
- How it compares with alternatives.
- How any stated time or detour constraint affected the ranking.

The optional model explanation step selects a constrained emphasis based on supplied route facts. The backend renders the final explanation from validated values and landmark metadata.

## 8. Task examples

The following examples illustrate the intended behavior for the Grand Central Terminal → Bryant Park sample routes recorded in the project:

| Route | Distance | Estimated walking time | Intermediate places |
|---|---:|---:|---|
| Direct 42nd Street Pedestrian Route | 686 m | 8.2 min | None |
| Via One Vanderbilt Transit Hall | 688 m | 8.4 min | 1 Vanderbilt |
| Art Deco Architectural Loop | 1,102 m | 13.6 min | Pershing Square Building, Chrysler Building |

**Traveler: "I love architecture but don't have much time."**  
The route through 1 Vanderbilt offers an architectural point of interest while remaining close to the direct route's estimated duration.

**Traveler: "I need to get to Bryant Park quickly."**  
The direct route is favored because it has the shortest recorded distance and estimated duration among these examples.

**Traveler: "I want to see as many interesting buildings as possible."**  
The architecture-focused route is favored because it includes Pershing Square Building and Chrysler Building, with a longer walking estimate.

These are examples of the same route comparison logic responding to different preference weights and constraints.

## 9. Scope

The current scoring dimensions are based on route geometry, route metrics, and the landmark catalogue. Live crowd levels, street quietness, greenery quality, safety, accessibility, pavement condition, and live closures are outside the current data model and are not inferred by the engine.
