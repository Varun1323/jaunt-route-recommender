# Geographic Research Log

**Research date:** 2026-11-20 (client local date, Asia/Calcutta)  
**Status:** Initial collection and five-landmark verification reviews are complete; systematic verification of the remaining four landmarks and all pedestrian access points is pending.

## Environment

- Python 3.12.5.
- Git 2.52.0.windows.1 is installed; the workspace is not a Git repository (`git status` returned “not a git repository”).
- Workspace was initially empty. No dependencies were installed.
- Early attempts to reach public OSM and Valhalla hosts failed DNS resolution. Later targeted Overpass, NYC Open Data, Nominatim, and direct OSM API requests did return candidate data. Valhalla routes have still not been obtained.

## Study area and landmarks

Candidate landmarks are supported by the sources below. The public-facing descriptions are concise summaries of official or preservation documentation, not claims based on an on-site inspection.

| ID | Landmark | Address | Architectural relevance | Historical relevance | Visitor/viewing status |
|---|---|---|---|---|---|
| `grand_central_terminal` | Grand Central Terminal | 71–105 East 42nd Street (visitor address also published as 89 E 42nd Street) | LPC identifies it as a major French Beaux-Arts work and an engineering solution combining functional terminal planning with monumental design. | Present terminal completed in 1913 after the earlier depot; a key landmark and catalyst for Terminal City. | Public attraction; official visitor page states terminal is open daily. Interior access is subject to operating conditions. |
| `pershing_square_building` | Pershing Square Building | 125 Park Avenue (also addresses on E 41st/E 42nd and Park Ave) | 1921–23 Lombard Revival design, varied brick and terra-cotta facades, distinctive un-setback tower form. | Connected to East Midtown subway construction and Terminal City; replaced Grand Union Hotel site. | Exterior viewing point; office building. Public/subway access points are not equivalent to general visitor access. |
| `chrysler_building` | Chrysler Building | 405 Lexington Avenue | LPC landmark; iconic Art Deco tower and crown. | Built during the late-1920s skyscraper competition and associated with Walter P. Chrysler; LPC designation report is the authoritative record. | Exterior viewing point; no general interior attraction assumed. |
| `graybar_building` | Graybar Building | 420 Lexington Avenue (also 420–430 Lexington Avenue) | Sloan & Robertson, 1925–27; Art Deco and Byzantine decorative program, monumental reliefs, sculpted rat details, stepped massing. | Terminal City building; Graybar Electric headquarters from 1927–1982; landmark designated in 2016. | Exterior viewing point. Public passage/transport connections exist, but do not imply general building access. |
| `chanin_building` | Chanin Building | 122 East 42nd Street (also 374 Lexington Avenue) | Art Deco, built 1927–29; Sloan & Robertson with sculptural decoration by Rene Chambellan. | Part of the 1920s Midtown/Terminal City-era skyscraper development. | Exterior viewing point; office building. |
| `daily_news_building` | Daily News Building | 220–226 East 42nd Street | Raymond Hood design, Art Deco landmark with notable public lobby and monumental globe. | Built 1929–30 as home and symbol of the Daily News; NYC and National Historic Landmark designations. | Exterior viewing point; preservation source reports lobby open to public. Verify current visitor access before recommending entry. |
| `one_vanderbilt` | 1 Vanderbilt (One Vanderbilt) | 1 Vanderbilt Avenue, Manhattan | Contemporary office/observation tower; source review is pending. | East Midtown development adjacent to Grand Central; detailed history/source review is pending. | Exterior viewing point; no general interior access assumed. |
| `stephen_a_schwarzman_building` | Stephen A. Schwarzman Building (NYPL) | 476 Fifth Avenue, Manhattan | Beaux-Arts civic library designed by Carrere and Hastings. | Opened in 1911 as the central research library. | Public attraction; current access and hours require confirmation. |
| `bryant_park` | Bryant Park | Between 40th–42nd Streets and Fifth–Sixth Avenues | Major Midtown public space behind the library; designed/managed public park with programmed amenities. | Site history includes potter’s field, Croton Reservoir area, and later restoration; BPC formed in 1980 to restore and operate the park. | Public attraction; open daily year-round subject to changing hours, weather, and maintenance closures. |

The active set contains nine landmarks, including NYPL and 1 Vanderbilt. The NYPL reference point (40.753014, -73.9817686) is only a spatial search anchor. The 1 Vanderbilt Nominatim address point is likewise only a search anchor. Neither is a verified coordinate or entrance. The OSM candidate report records a failed One Vanderbilt batch, but a later direct OSM way geometry and NYC footprint were compared separately. NYPL has historic OSM no-match metadata plus a locally cached OSM way and a NYC footprint candidate.

## Coordinate verification

No coordinates are approved in `backend/data/landmarks.json`: **0 of 9 landmark coordinates are verified**, and all are still null. `landmark_candidates_v2.json` contains OSM candidates for seven landmarks and an unresolved failed One Vanderbilt batch; the current file has no NYPL record, although NYPL raw OSM evidence exists. `nyc_building_candidates.json` contains 35 candidates for 1 Vanderbilt and 56 for NYPL. These footprints and their representative geometry do not establish a routeable point or pedestrian entrance by themselves. Review recommendations and evidence are tracked separately in `verification-log.md`.

## Routing result

No successful Valhalla route result is present. The only current route artifact is a refusal record because Grand Central lacks verified WGS84 coordinates and evidence. Therefore no genuine pedestrian route geometry, distance, or duration has been obtained; `backend/data/routes/` contains only `.gitkeep`. The three route candidates remain pending. See `route-methodology.md` for the query protocol and detour calculations, to apply only after successful responses.

## Reproducible collection tooling (2026-11-20)

The standard-library-only `collect_geodata.py` reads active IDs from `landmarks.json`, queries Overpass, optionally performs rate-limited Nominatim searches, stores raw responses under `backend/data/raw/osm/`, and writes OSM candidate records to `backend/data/landmark_candidates_v2.json`. Candidates remain unverified; identity and entrance suitability need human review. Its cache expires after seven days by default (`--cache-hours 0` forces a new query), and `JAUNT_OVERPASS_URL` selects the endpoint. Known OSM IDs should use the direct OSM API rather than repeated Overpass discovery.

`collect_nyc_buildings.py` independently queried NYC Open Data BUILDING (`5zhs-2jue`) using a 200 m `within_circle(the_geom,...)` spatial filter and selected full polygon geometry and attributes including `NAME`, `BIN`, `DOITT_ID`, `BASE_BBL`, and `OBJECTID`. It did not download the citywide million-row file or require `NAME`. NYPL and 1 Vanderbilt candidates are stored separately in `backend/data/nyc_building_candidates.json`; all remain unverified and are never mixed with OSM IDs. Subsequent targeted NYC queries by BIN were used in the first verification batch; their complete matching responses were not added to the raw local cache.

`fetch_routes.py` uses Valhalla pedestrian costing and decodes only provider-returned polyline6 geometry to GeoJSON. It requires verified origin and destination coordinates in `landmarks.json`, including reviewer evidence, and a human-prepared `route_waypoints.json` with verified access points for the architecture and alternative-street candidates. It refuses missing/unverified inputs, network failures, rate limits, malformed summaries, and missing geometry; it never uses straight lines. Raw request/response JSON is saved under `backend/data/raw/valhalla/`; successful route GeoJSON goes into `backend/data/routes/`.

Exact PowerShell commands and input schemas are documented in `backend/scripts/README.md`. Local tests and geometry comparisons are offline. OSM collection requires Overpass (or direct OSM API for known IDs); NYC footprint collection requires NYC Open Data and Nominatim for the 1 Vanderbilt search anchor. Routes require Valhalla after human review supplies verified coordinates and waypoints. No collection or route requests were made during the documentation handoff task.

## Evidence and source notes

- [Grand Central Terminal visitor information](https://grandcentralterminal.com/visit/) supports the public address and visitor status.
- [Grand Central Terminal LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0266.pdf) supports architecture, history, and official landmark address.
- [Chrysler Building LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0992.pdf) supports landmark address and historical/architectural significance.
- [Pershing Square Building LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/2556.pdf) supports address, style, history, and landmark status.
- [Graybar Building LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/2554.pdf) supports address, style, history, and landmark status.
- [Chanin Building LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20240625/374-Lexington-Avenue.pdf) supports its address, construction dates, architect, and style; this is an LPC project presentation, not a full designation report.
- [Daily News Building LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20181120/220-East-42nd-Street-final.pdf) supports landmark building context; [Docomomo US register](https://docomomo-us.org/register/daily-news-building) provides the address, design authorship, designations, and public lobby note.
- [NYPL Visitor Center](https://visitorcenter.nypl.org/) supports visitor access and architecture; [NYPL landmark interior report](https://s-media.nyc.gov/agencies/lpc/lp/2592.pdf) supports the interior's designation and architectural significance.
- [NYC BUILDING dataset](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue) and [NYC OTI building-footprints metadata](https://github.com/CityOfNewYork/nyc-geo-metadata/blob/main/Metadata/Metadata_BuildingFootprints.md) describe the polygon footprint dataset and identifiers. The collector and targeted first-batch comparisons have queried the API.
- Batch verification recommendations, source IDs, geometry metrics, and evidence retention limits are in [verification-log.md](verification-log.md).
- [Bryant Park hours and directions](https://bryantpark.org/the-park/hours-and-directions) and [park history](https://bryantpark.org/the-park/history) support location, visitor access, and history; [BPC about page](https://bryantpark.org/about-us) supports management history.
