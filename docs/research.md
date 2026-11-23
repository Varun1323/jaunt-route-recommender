# Researching the Walking-Route Problem

**Study area:** Midtown Manhattan, New York City  
**Research focus:** Places of architectural and historical interest around Grand Central Terminal, Bryant Park, and the surrounding Midtown pedestrian network.

## 1. Research questions

The research addressed the questions in the Jaunt task:

- What pedestrian routes can connect selected places?
- Which nearby buildings, parks, and institutions add value to a walk?
- Which sources provide reliable place descriptions, addresses, and geographic data?
- How can walking time and additional detour cost be estimated?
- How should a route's sightseeing value be compared with its additional time and distance?

## 2. Place research

The following places form the core researched set. Descriptions summarize official institutional material, New York City Landmarks Preservation Commission material, and geographic records.

| Place | Address / location | Why it is interesting | Main source |
|---|---|---|---|
| Grand Central Terminal | 89 E 42nd St, Manhattan | Major Beaux-Arts rail terminal, completed in 1913; monumental public interior and a defining part of Terminal City | [NYC LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0266.pdf), [visitor information](https://grandcentralterminal.com/visit/) |
| Pershing Square Building | 125 Park Ave, Manhattan | 1920s Lombard Revival office building with a distinctive tower form and decorative brick and terra-cotta details | [NYC LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/2556.pdf) |
| Chrysler Building | 405 Lexington Ave, Manhattan | Landmark Art Deco skyscraper, notable for its stainless-steel crown and sculptural ornament | [NYC LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0992.pdf) |
| Chanin Building | 122 E 42nd St, Manhattan | Art Deco skyscraper designed by Sloan & Robertson, with sculptural ornament by Rene Chambellan | [NYC LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20240625/374-Lexington-Avenue.pdf) |
| Daily News Building | 220 E 42nd St, Manhattan | Raymond Hood's Art Deco landmark, known for its monumental globe and historic newspaper headquarters | [NYC LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20181120/220-East-42nd-Street-final.pdf), [Docomomo US](https://docomomo-us.org/register/daily-news-building) |
| 1 Vanderbilt | 1 Vanderbilt Ave, Manhattan | Contemporary supertall office tower beside Grand Central; its form creates a contrast with the area's historic skyscrapers | [NYC Open Data BUILDING](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue), [building website](https://www.onevanderbilt.com/) |
| Stephen A. Schwarzman Building (NYPL) | 476 Fifth Ave, Manhattan | Beaux-Arts research library designed by Carrere and Hastings; opened in 1911 and forms the western edge of Bryant Park | [NYPL Visitor Center](https://visitorcenter.nypl.org/), [NYC LPC interior report](https://s-media.nyc.gov/agencies/lpc/lp/2592.pdf) |
| Bryant Park | Between W 40th–42nd St and Fifth–Sixth Ave, Manhattan | Major public green space with a long civic history, public seating, seasonal programming, and a strong relationship to NYPL | [Park history](https://bryantpark.org/the-park/history), [hours and directions](https://bryantpark.org/the-park/hours-and-directions) |

Additional visitor-interest locations in the application catalogue include Times Square, Rockefeller Center, St. Patrick's Cathedral, and the Museum of Modern Art. They broaden the selectable journey area and provide further context for the UI's landmark catalogue.

## 3. Geographic and routing data

### OpenStreetMap

OpenStreetMap supplies mapped features, names, tags, and pedestrian-network geometry. The project retrieves selected features through Overpass and the direct OSM API, preserving OSM element types and IDs with their source records.

### NYC Open Data — BUILDING

The NYC BUILDING dataset (Socrata identifier `5zhs-2jue`) provides building-footprint polygons and municipal attributes such as BIN, DOITT_ID, and BBL. The project uses spatial queries to retrieve relevant footprints instead of downloading the citywide dataset.

### Official and preservation sources

NYC Landmarks Preservation Commission reports are used for architectural descriptions, designation context, and historical background. Institutional and park websites are used for first-party visitor information and place history.

### Valhalla

Valhalla's pedestrian profile supplies route geometry, distance, and estimated walking duration over an OSM-derived pedestrian network. The application stores provider-returned geometry as GeoJSON and retains raw request/response evidence.

## 4. How walking time and detours are evaluated

Valhalla returns route distance and estimated duration. The project uses these returned route metrics rather than estimating time from straight-line distance.

For a candidate route:

- **Additional distance** = candidate route distance − direct route distance.
- **Additional walking time** = candidate estimated duration − direct route estimated duration.
- **Detour ratio** = candidate route distance ÷ direct route distance.

Sightseeing or viewing time is kept separate from walking time. The prototype does not add assumed dwell time to Valhalla's walking estimate.

## 5. Deciding whether a place is worth a detour

A place is useful when it matches the traveler's interests and the route reaches it without an excessive increase in distance or time. The route selector therefore considers geographic position relative to the direct route, endpoint clearance, waypoint separation, and detour bounds.

The recommendation engine then compares actual route metrics and the intermediate landmarks on each candidate. More landmarks do not automatically make a route better: a short route can win for a time-focused traveler, while a longer route can win when its additional architectural or historical value matches the request and remains within the traveler's constraints.

## 6. Source quality and trust

The project uses sources according to the information they are best positioned to provide:

1. **Official landmark and institutional sources** for history, architecture, visitor context, and addresses.
2. **NYC Open Data** for municipal building-footprint geometry and city identifiers.
3. **OpenStreetMap** for mapped pedestrian networks and geographic feature geometry, with community-maintained data provenance retained.
4. **Valhalla** for computed pedestrian route geometry, distance, and estimated duration.

No single source is treated as authoritative for every field. For example, a building footprint identifies a structure, while a pedestrian route engine calculates a path through its mapped network. The application keeps those roles distinct.

## 7. Research artefacts

- `backend/data/landmarks.json` — landmark descriptions and catalogue metadata.
- `backend/data/pedestrian_access_points.json` — documented approach/access descriptions.
- `backend/data/pedestrian_access_point_candidates.json` — routing candidate points.
- `backend/data/nyc_building_candidates.json` — NYC building-footprint records.
- `backend/data/raw/osm/` and `backend/data/raw/nyc_open_data/` — source response archives.
- `backend/data/routes/` — route GeoJSON and route metadata.
- [Data sources and usage](data-sources.md) — source policies, attribution, and licensing.
- [Route methodology](route-methodology.md) — route generation and validation procedure.
