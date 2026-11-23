# Pedestrian Route Generation and Methodology

## Objective

The routing layer supplies genuine pedestrian-network route candidates for the recommendation engine. It uses Valhalla's pedestrian costing profile and stores the returned route shape as GeoJSON. The application does not draw straight lines between landmarks and present them as walking routes.

## Route data flow

1. Read the selected origin and destination from the landmark catalogue.
2. Resolve the corresponding routing candidate points.
3. Submit a pedestrian route request to Valhalla.
4. Decode the returned polyline6 shape into GeoJSON coordinates in `[longitude, latitude]` order.
5. Validate route geometry, journey identity, endpoint proximity, distance, duration, and provider metadata.
6. Save the route and its provenance in the route cache.
7. Load the validated route candidates for scoring and display.

## Direct route and alternatives

The direct route is retained as the baseline candidate. The route generator can also create intermediate-landmark routes and request native alternatives where the selected generation strategy supports them.

For general journeys, candidate waypoints are selected geographically using the direct route's geometry and a bounded set of rules:

- A candidate must be positioned meaningfully away from the origin and destination.
- A candidate should add a distinct point of interest relative to the direct corridor.
- Selected waypoints must be sufficiently separated from each other.
- The candidate detour is bounded relative to the direct journey.

The generator does not add a waypoint merely to increase the number of routes. A short journey may have only one useful candidate. For Grand Central Terminal → Bryant Park, curated route definitions preserve the direct, 1 Vanderbilt, and architecture-focused examples.

## Route validation

The route loader and generator validate:

- GeoJSON FeatureCollection/Feature structure and LineString geometry.
- Finite WGS84 coordinate values and a usable route shape.
- Positive finite distance and estimated duration.
- Origin/destination identity and endpoint proximity.
- Pedestrian costing and Valhalla provenance.
- Route distinctness and detour bounds for newly generated alternatives.

Route candidates that fail validation are not included in recommendation scoring. Raw provider requests and responses are retained to support reproducibility and diagnosis.

## Detour calculations

For each candidate route (r), relative to the direct route (d):

- Additional distance: (Delta D = D_r - D_d).
- Additional estimated walking time: (Delta T = T_r - T_d).
- Detour ratio: (R = D_r / D_d).

These values use the route engine's own metrics and a common provider/profile. The system does not mix straight-line distance with network distance when calculating route detours.

A candidate must be meaningfully different from the direct route or previously accepted alternatives. A different waypoint label alone does not make a route distinct. Current distinctness checks use distance difference and route-geometry divergence.

## Walking time versus visit time

The duration returned by Valhalla represents estimated walking time. Time spent viewing a building, entering an institution, waiting, or taking photographs is not included. This separation prevents the interface from presenting an assumed sightseeing duration as a routing-engine estimate.

## Route cache and reproducibility

Route files are stored in `backend/data/routes/`. Raw requests, responses, and batch reports are stored under `backend/data/raw/valhalla/`.

From the repository root, the route-generation command is:

```powershell
python .\backend\scripts\fetch_routes.py --all
```

The generator supports resumable execution, request delays, retries, backoff, and an explicit force option. Existing valid cache entries are reused by default.

## Routing provider

Valhalla is used because it returns pedestrian-network geometry and route metrics. The public demo is a shared research service; requests are sequential and throttled. The route cache allows the prototype to serve precomputed journeys without requiring a live routing request for every page interaction.

## Interpretation

Valhalla's route shape is the path calculated over its pedestrian graph at request time. Its duration is an estimate. The prototype uses these outputs for route comparison and demonstration; it does not claim live traffic, crowd, safety, closure, or accessibility analysis.
