# Pedestrian Route Research Methodology

## Current outcome

On 2026-11-21, `backend/scripts/fetch_routes.py --all --delay 1.2 --retries 3` generated or reused a validated Valhalla pedestrian baseline for all **72 directed journeys**. The report at `backend/data/raw/valhalla/route_coverage_report.json` records 72 validated route generations, zero failures, and 74 valid cached routes (including two pre-existing alternatives for Grand Central Terminal → Bryant Park).

The generator now supports multi-route generation for every journey, but **no new live alternative routes have been requested or written** as part of this code change. The persisted cache therefore remains the 74 baseline/legacy records described above until an approved `--all` run is executed. A full run would reuse all 72 direct baselines and request alternatives only where the geographic policy finds a relevant intermediate landmark; the deterministic pre-run estimate is **106 additional requests** (48 journeys with two, 10 with one, 14 with none), before post-fetch duplicate/detour rejection.

These are validated routing-engine responses using **unverified prototype access-point candidates**. All nine endpoint candidates remain `candidate_unconfirmed`; route metadata therefore remains `prototype_candidate_unverified`. Do not describe the 72 results as field-verified routes or verified public entrances. Raw Valhalla requests and responses are preserved under `backend/data/raw/valhalla/`.

## Valhalla generation and validation protocol

The Valhalla documentation describes the route service and pedestrian costing, and the project README identifies FOSSGIS's public demo at `https://valhalla1.openstreetmap.de`. Each route request follows this protocol:

1. Use the nine stored candidate access points as route endpoints. They are projections to the connected outdoor pedestrian network but remain unverified; this limitation is included with every route.
2. Submit `POST https://valhalla1.openstreetmap.de/route` with `costing: "pedestrian"`, `units: "kilometers"`, polyline6 shape output, and identifying client headers. Preserve the request and raw response.
3. Decode the returned shape to GeoJSON `[longitude, latitude]` coordinates. Validate at least two finite WGS84 positions, endpoint proximity within 150 m, positive finite distance/duration, successful Valhalla status, and requested origin/destination identity.
4. Persist a GeoJSON `FeatureCollection` with one `LineString`, provenance, route IDs, candidate endpoint coordinates, provider/retrieval data, and the unverified-coordinate disclosure.
5. On cache reuse, repeat structural, metric, provenance, journey-ID, and current-candidate endpoint checks. Invalid or stale entries are not counted and are regenerated.

The public demo's README says fair use applies and requests an identifying client header for published end-user applications. Treat it as a research/demo service, not a production SLA. Batch requests are sequential and throttled; do not parallelize or force-refresh unnecessarily.

## Route and alternative policy

Each journey is built in two stages:

1. Request the **direct baseline** first and validate it.
2. Use the direct route's actual geometry (falling back to the straight endpoint corridor only for candidate ranking if the direct route is unavailable) to select up to two intermediate landmarks. Each selected landmark produces one genuine Valhalla via-point request.

The existing Grand Central Terminal → Bryant Park alternatives (`via_vanderbilt`, `architecture`) are seeded as curated definitions for that pair and are retained for cache/API compatibility. Every other pair uses the generic geographic selector.

A candidate intermediate landmark is eligible only when all of these hold:

- straight-line via distance is at most `2.0×` the direct distance, capped by `+500 m` extra;
- it is at least `50 m` from both endpoints;
- it is at least `20 m` laterally off the direct corridor;
- it is at least `100 m` from any other selected waypoint.

Eligible candidates are ranked by lateral offset minus a detour penalty, with landmark ID as the deterministic tie-break. Short adjacent journeys can legitimately produce no alternative. The selector never forces a detour merely to increase route count.

After a response is validated, newly generated alternatives are rejected when they are:

- **excessive detours**: longer than the direct route by more than `750 m` or `2.0×`; or
- **duplicate paths**: not meaningfully distinct from the direct route or an already accepted alternative.

Meaningful difference now requires a real distance difference of at least `75 m` / `15%`, or at least 35% of vertices more than 35 m from the other geometry. A different `via_waypoints` label on the same corridor is **not** sufficient. This closes the previous loophole where a landmark stop that did not change the path counted as an alternative.

A rejected newly generated route is removed from the route cache, but its raw request/response evidence is retained and a `{origin}__{destination}__{route_id}__rejected.json` marker records the reason and comparison metrics. A route file that existed before the run is never deleted; a pre-existing duplicate is preserved and flagged rather than silently discarded. An alternative failure or rejection never invalidates a successful direct baseline or terminates the batch.

- `direct`: origin and destination routed by Valhalla pedestrian costing; it is a network route, not a straight-line path.
- Via-point alternatives are still subject to access uncertainty: a returned pedestrian path does not prove that an interior is publicly accessible. Do not force access into private interiors.

For via-point candidates, use Valhalla's route API with ordered intermediate locations. Record waypoint order and the requested preference inputs; engine-reported results are not guaranteed to represent a globally optimal or safest path.

## Detours and sightseeing time

After all route results are successfully retrieved using the same provider, profile, endpoint points, and units, calculate:

- Additional distance (m) = candidate walking distance − direct walking distance.
- Additional estimated walking time (s) = candidate route estimate − direct route estimate.

Never calculate these using Euclidean distance or a mix of providers. A negative value can occur if the candidate changes street network or endpoints; investigate and explain it rather than clamping to zero.

Keep estimated walking time separate from viewing time. No sightseeing/viewing duration is currently researched or assumed. If later included, store it as an independently sourced or user-selected dwell-time input, and report total itinerary time as walking estimate plus dwell-time inputs.

## Reproduction command

From the project root, generate or resume all 72 journeys:

```powershell
python .\backend\scripts\fetch_routes.py --all
```

`--delay`, `--retries`, and `--backoff` configure sequential request throttling and bounded retry behavior; `--force` bypasses valid cache entries intentionally. Progress, valid/alternative counts, rejection IDs, and per-journey failure reasons are saved in `backend/data/raw/valhalla/route_coverage_report.json` (schema version 2). A network or validation failure never triggers a synthetic fallback. The report is written after each journey, so an interrupted run resumes without discarding successful routes.
