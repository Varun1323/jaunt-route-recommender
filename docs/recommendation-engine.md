# Jaunt Recommendation Engine Documentation

**Date:** 2026-11-21  
**Module:** `backend/scripts/recommendation_engine.py` & `backend/scripts/preference_parser.py`  
**Status:** Phase 9.1 integrated; final offline test results are recorded in `docs/progress.md`.

---

## 1. Objective and Architecture

The Jaunt Recommendation Engine evaluates available pedestrian walking routes against a traveler's natural-language or structured preferences. Preference interpretation may use OpenRouter; ranking remains deterministic and is strictly grounded in real route properties:
- Walking distance (metres) from genuine Valhalla pedestrian routes.
- Estimated duration (seconds/minutes) from genuine Valhalla pedestrian routes.
- Intermediate landmarks visited along each route.
- Landmark relevance (architectural, historical) sourced from `backend/data/landmarks.json`.

The engine avoids hardcoding responses for specific prompts; the same scoring formula and constraint ordering evaluate any validated route set against any preference vector.

OpenRouter is implemented with Python's standard-library HTTP client in `backend/scripts/openrouter_client.py`; no SDK or additional dependency is required. Configuration is read from the process environment first and then `backend/.env`, without mutating or overwriting pre-existing environment variables. `OPENROUTER_API_KEY` is backend-only; the frontend receives only an AI status and safe explanation text. A missing key uses local parsing. Provider errors and timeouts are surfaced in `ai_status` while preserving the local parser/scorer result. Structured preferences are range-validated before scoring.

The explanation-focus request receives only the selected/ranked route IDs and names, actual route distances and estimated durations, backend-calculated extra distance/time versus the shortest/fastest options, supplied preference fields, and place names/categories/descriptions already returned by the recommendation engine. It does not receive route geometries or coordinates. The model selects from a constrained set of factual emphasis labels; the backend renders the human-readable explanation using validated route values, so the model cannot add route details. Deterministic backend scoring, not the model, selects the route.

The recommendation API first reads strict-validated cached routes, then uses OpenRouter's structured preference parser (or the existing local parser) to select a set of justified generation options. The backend computes an allowlist based on cache coverage, scored route/place facts, and available alternative slots. OpenRouter may choose only `none`, `landmark`, `native`, or `both` from that allowlist. On provider failure, the local parser and policy choose the fallback strategy. The selected strategy calls the existing `fetch_all_routes()` with `skip_existing=True`; generated GeoJSON is reloaded with strict validation before scoring. The API reports generated route IDs and failures. Valhalla, not the model, supplies route geometry, distance, and duration.

```
Free-text Query  ──►  preference_parser.py  ──►  UserPreferences (structured weights & constraints)
                                                         │
                                                         ▼
Routes (*.geojson) & Landmarks (landmarks.json) ──►  recommendation_engine.py ──► Ranked Recommendations & Explanation
```

---

## 2. Preference Dimensions

The `preference_parser.py` module converts unstructured text queries into a structured `UserPreferences` object supporting:

| Dimension | Range | Description / Trigger Phrases |
|---|---|---|
| `architecture` | `0.0`–`1.0` | Interest in historic architecture, facades, historic landmarks, Art Deco, Beaux-Arts styles. |
| `history` | `0.0`–`1.0` | Interest in historical places, heritage, monuments, and history. |
| `shortest_distance` | `0.0`–`1.0` | Preference for minimizing distance ("shortest walk", "shortest route", "least distance"). |
| `minimum_walking_time` | `0.0`–`1.0` | Preference for speed / brevity ("quick", "fast", "fastest", "in a hurry", "least time"). |
| `more_landmarks` | `0.0`–`1.0` | Preference for seeing many sights / points of interest ("as many as possible", "more landmarks"). |
| `min_landmarks` | `int` ($\ge 0$) | Minimum landmark count constraint ("at least one landmark"). |
| `max_duration_minutes` | `float` or `None` | Explicit time ceiling (e.g. "under 10 minutes") or qualitative default (e.g. "don't have much time" $\to 10.0$ min). |
| `max_detour_ratio` | `1.0`–`2.0` or `None` | Maximum allowed route-distance ratio vs. the shortest available route (e.g. "up to 25% longer" $\to 1.25$). The hard cap cannot exceed the route-generation policy. |
| `excluded_categories` | subset of categories | Hard exclusions for architecture, history, or intermediate landmarks. |

If a query is empty, missing, or neutral, preferences default to a balanced walk ($w_{\text{dist}} = 0.5, w_{\text{time}} = 0.5$).

`unsupported_preferences` records expressed qualities for which Jaunt lacks route-level evidence. Greenery and quietness are currently identified and disclosed, not scored. A route is never treated as quiet or green solely because of a model claim or landmark name.

---

## 3. Deterministic Route Scoring

### 3.1 Normalized Metrics
For a set of candidate routes $\mathcal{R}$, raw metrics are normalized across available options to $[0.0, 1.0]$:

1. **Distance Efficiency ($S_{\text{dist}}$):**
   $$S_{\text{dist}}(r) = 1.0 - \frac{D_r - D_{\min}}{D_{\max} - D_{\min}} \quad (\text{if } D_{\max} > D_{\min} \text{ else } 1.0)$$

2. **Time Efficiency ($S_{\text{time}}$):**
   $$S_{\text{time}}(r) = 1.0 - \frac{T_r - T_{\min}}{T_{\max} - T_{\min}} \quad (\text{if } T_{\max} > T_{\min} \text{ else } 1.0)$$

3. **Landmark Quantity ($S_{\text{landmarks}}$):**
   $$S_{\text{landmarks}}(r) = \frac{L_r}{L_{\max}} \quad (\text{if } L_{\max} > 0 \text{ else } 0.0)$$

4. **Architectural Relevance ($S_{\text{arch}}$):**
   Each intermediate landmark contributes according to verified categories in `landmarks.json` (architecture $= +1.0$, historic landmark $= +0.5$).
   $$S_{\text{arch}}(r) = \frac{A_r}{A_{\max}} \quad (\text{if } A_{\max} > 0 \text{ else } 0.0)$$

5. **Historical Relevance ($S_{\text{history}}$):**
   $$S_{\text{history}}(r) = \frac{H_r}{H_{\max}} \quad (\text{if } H_{\max} > 0 \text{ else } 0.0)$$

### 3.2 Weighted Base Score
$$\text{BaseScore}(r) = \frac{w_{\text{dist}} S_{\text{dist}}(r) + w_{\text{time}} S_{\text{time}}(r) + w_{\text{arch}} S_{\text{arch}}(r) + w_{\text{history}} S_{\text{history}}(r) + w_{\text{landmarks}} S_{\text{landmarks}}(r)}{w_{\text{dist}} + w_{\text{time}} + w_{\text{arch}} + w_{\text{history}} + w_{\text{landmarks}}}$$

If `min_landmarks > 0` and a route has fewer landmarks than required while alternatives meet the threshold, its landmark and architectural components are set to $0.0$.

### 3.3 Time Constraints and Feasibility Penalties
Let $T_{\text{limit}}$ be `max_duration_minutes`. A route is feasible if $T_{r,\text{min}} \le T_{\text{limit}}$.

- **Feasible alternative exists:**
  Infeasible routes ($T_{r,\text{min}} > T_{\text{limit}}$) receive a steep penalty scaling with the overtime ratio:
  $$\text{PenaltyFactor} = 0.15 \times \max\left(0.1, 1.0 - \frac{T_{r,\text{min}} - T_{\text{limit}}}{T_{\text{limit}}}\right)$$
  $$\text{FinalScore}(r) = \text{BaseScore}(r) \times \text{PenaltyFactor}$$

  Time feasibility is applied before soft scores when any route meets the limit. Other satisfiable hard constraints (minimum landmarks, category exclusions, and maximum detour) are applied in that order before soft scores. If no route satisfies a hard constraint, all available routes remain ranked and the explanation identifies the unmet constraint.

- **No feasible route exists ($T_{\text{limit}} < T_{\min}$):**
  When all routes exceed $T_{\text{limit}}$, routes are ranked by proximity to the limit:
  $$\text{FinalScore}(r) = \text{BaseScore}(r) \times \max\left(0.2, \frac{T_{\text{limit}}}{T_{r,\text{min}}}\right)$$
  The engine recommends the fastest available route and issues a clear time limit warning in the explanation.

---

## 4. Human-Readable Explanation Generation

The explanation dynamically details:
1. **Primary Winning Factors:** Explicitly states which preferences favored the top route (e.g. shortest distance, fastest time, visits specific named landmarks, fits within time budget).
2. **Comparison with Runner-up:** Contrasts distance or landmark trade-offs against the second-place route.
3. **Time Limit Enforcement:** Identifies any routes penalized or excluded for exceeding the traveler's stated time limit.

---

## 5. Example Queries and Outputs

Evaluating the 3 retrieved pedestrian routes for Grand Central Terminal $\to$ Bryant Park:
- `direct`: 686.0 m, 494.9 s (8.2 min), 0 intermediate waypoints
- `via_vanderbilt`: 688.0 m, 507.0 s (8.4 min), 1 intermediate waypoint (1 Vanderbilt)
- `architecture`: 1102.0 m, 816.7 s (13.6 min), 2 intermediate waypoints (Pershing Square Building, Chrysler Building)

### Example 1: "I love architecture but don't have much time."
- **Recommended Route:** `via_vanderbilt` (Via One Vanderbilt Transit Hall)
- **Distance & Time:** 688 m | 8.4 min
- **Landmarks:** 1 Vanderbilt
- **Scores:** `via_vanderbilt`: 0.6129 | `direct`: 0.4444 | `architecture`: 0.0532
- **Explanation:** *"Recommended 'Via One Vanderbilt Transit Hall' because it provides a fast walking time of 8.4 minutes, and it visits notable architectural landmarks (1 Vanderbilt), and it fits comfortably within your 10-minute time budget (8.4 min). In comparison, 'Direct 42nd Street Pedestrian Route' visits fewer architectural landmarks (0 vs. 1). Route alternatives exceeding your 10-minute limit were penalized ('Art Deco Architectural Loop' (13.6 min))."*

### Example 2: "I want the shortest possible walk."
- **Recommended Route:** `direct` (Direct 42nd Street Pedestrian Route)
- **Distance & Time:** 686 m | 8.2 min
- **Landmarks:** None
- **Scores:** `direct`: 1.0000 | `via_vanderbilt`: 0.9952 | `architecture`: 0.0000
- **Explanation:** *"Recommended 'Direct 42nd Street Pedestrian Route' because it is the shortest route at 686 metres. It saves 2 metres compared to 'Via One Vanderbilt Transit Hall'."*

### Example 3: "I want to see as many interesting buildings as possible."
- **Recommended Route:** `architecture` (Art Deco Architectural Loop)
- **Distance & Time:** 1102 m | 13.6 min
- **Landmarks:** Pershing Square Building, Chrysler Building
- **Scores:** `architecture`: 1.0000 | `via_vanderbilt`: 0.4167 | `direct`: 0.0000
- **Explanation:** *"Recommended 'Art Deco Architectural Loop' because it visits notable architectural landmarks (Pershing Square Building, Chrysler Building), and it maximizes points of interest (2 intermediate landmarks). In comparison, 'Via One Vanderbilt Transit Hall' visits fewer architectural landmarks (1 vs. 2)."*

### Example 4: "I prefer a quick walk with at least one interesting landmark."
- **Recommended Route:** `via_vanderbilt` (Via One Vanderbilt Transit Hall)
- **Distance & Time:** 688 m | 8.4 min
- **Landmarks:** 1 Vanderbilt
- **Scores:** `via_vanderbilt`: 0.7890 | `direct`: 0.6250 | `architecture`: 0.3750
- **Explanation:** *"Recommended 'Via One Vanderbilt Transit Hall' because it provides a fast walking time of 8.4 minutes, and it maximizes points of interest (1 intermediate landmark), and it fulfills your preference for at least one interesting landmark."*

---

## 6. Limitations & Prototype Notes

1. **Unverified Prototype Coordinates:** Evaluated routes were generated using prototype coordinates projected onto OSM sidewalk centerlines (`verification_status: "prototype_candidate_unverified"`). Walk durations and distances are routing engine estimates from Valhalla pedestrian costing.
2. **No Live Crowd or Safety Modeling:** The engine does not evaluate crowd density, perceived safety, or pavement quality as no verified data sources exist for those factors.
3. **Intermediate vs. Endpoint Landmarks:** Landmarks at the start and end of the trip (Grand Central Terminal and Bryant Park) are common to all candidate routes; scoring specifically differentiates routes based on intermediate approach waypoints.
4. **Cache precedence:** When the same `(origin, destination, route_id)` exists in both a journey-scoped file and a legacy unscoped file, `load_routes()` prefers the journey-scoped file. Preferences trigger generation only when supported interests or unmet hard constraints justify more options and cache capacity remains.
5. **Route supply vs. ranking:** Route retrieval remains in `fetch_routes.py`; the API is responsible for a bounded strategy decision. Route geometry, actual metrics, policy bounds, idempotency, and distinctness checks remain enforced by the Valhalla integration.
6. **Optional AI dependency:** Without an OpenRouter key, or when OpenRouter errors/times out, recommendations still use the local parser and deterministic strategy. Configured requests may make up to three provider calls (preference interpretation, constrained strategy choice, and explanation focus); no live provider call was made for Phase 9.1 verification.
7. **Unsupported experiential qualities:** Current place data does not verify route-level quietness or greenery. Such preferences are surfaced as unsupported rather than being used to invent a route quality.
