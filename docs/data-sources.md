# Data Sources, Provenance, and Attribution

This document records the geographic, historical, and routing sources used by the Jaunt prototype and the role each source plays.

## Source register

| Source | Data used | Why it is appropriate | How the project uses it |
|---|---|---|---|
| NYC Landmarks Preservation Commission (LPC) | Designation reports and presentations | Municipal preservation records provide authoritative landmark context, addresses, architectural descriptions, and historical background | Supports place descriptions and architectural/history relevance |
| Official building and institutional websites | Visitor information, public-facing addresses, institutional history | First-party sources are best suited to describe the institution's own facilities and visitor information | Supports landmark descriptions and visitor context |
| New York Public Library | History, architecture, visitor spaces, and access information for the Stephen A. Schwarzman Building | The institution is the operator and primary source for its own visitor facilities | Supports NYPL place information |
| Bryant Park Corporation | Park location, history, hours, amenities, and management | The park operator is the primary source for park information | Supports Bryant Park place information |
| OpenStreetMap (OSM) | Mapped buildings, parks, pedestrian ways, feature tags, and geometry | Open, inspectable geographic data with element-level identifiers and downloadable geometry | Supplies pedestrian-network and feature geometry; source IDs and raw responses are retained |
| Overpass API | Targeted OSM feature queries | Allows selective retrieval of relevant OSM elements | Used by the geographic collection scripts with caching and request throttling |
| NYC Open Data BUILDING (`5zhs-2jue`) | Building footprint polygons and attributes including BIN, DOITT_ID, BASE_BBL, OBJECTID, construction year, and geometry source | City-maintained building dataset with full footprint geometry and municipal identifiers | Cross-source building geometry analysis and provenance |
| Nominatim | Place/address search results | Useful for geocoding and discovery | Used as a search input; a geocoder result is not treated as a building entrance |
| Valhalla | Pedestrian route geometry, distance, and estimated duration | Open-source routing engine with a pedestrian costing model and GeoJSON-compatible shape output | Generates the actual route geometry shown by the application |
| OpenRouter | Structured interpretation of traveler preferences and constrained explanation focus | Provides an LLM interface while allowing the application to validate structured output and retain deterministic scoring | Optional preference interpretation; backend controls allowed values and scoring |

## Place-specific source references

- Grand Central Terminal: [LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0266.pdf), [visitor information](https://grandcentralterminal.com/visit/).
- Pershing Square Building: [LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/2556.pdf).
- Chrysler Building: [LPC designation report](https://s-media.nyc.gov/agencies/lpc/lp/0992.pdf).
- Chanin Building: [LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20240625/374-Lexington-Avenue.pdf).
- Daily News Building: [LPC presentation](https://www.nyc.gov/assets/lpc/downloads/pdf/presentation-materials/20181120/220-East-42nd-Street-final.pdf), [Docomomo US register](https://docomomo-us.org/register/daily-news-building).
- NYPL: [Visitor Center](https://visitorcenter.nypl.org/), [LPC interior designation report](https://s-media.nyc.gov/agencies/lpc/lp/2592.pdf).
- Bryant Park: [hours and directions](https://bryantpark.org/the-park/hours-and-directions), [park history](https://bryantpark.org/the-park/history).
- 1 Vanderbilt: [official website](https://www.onevanderbilt.com/), [NYC BUILDING dataset](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue).

## Collection and data handling

- OSM records retain element type, OSM ID, tags, source URL, and raw response data.
- NYC Open Data records retain the dataset ID, source URL, municipal identifiers, original attributes, and polygon geometry.
- OSM IDs and NYC identifiers are stored in their own namespaces and are not interchanged.
- Geographic collection scripts use selective spatial queries, successful-response caching, retries, and request delays.
- Route records retain Valhalla provenance, request parameters, route identifiers, endpoint references, distance, duration, and GeoJSON geometry.
- Raw source responses are retained under `backend/data/raw/` to make collection decisions reproducible.

## Attribution and licensing

OpenStreetMap data is available under the Open Database License (ODbL). Maps and OSM-derived data should include attribution such as **© OpenStreetMap contributors**, linked to [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright). Review ODbL obligations when distributing a database derived from OSM.

NYC Open Data use is governed by the [NYC Open Data Terms of Use](https://opendata.cityofnewyork.us/overview/#terms-of-use). Dataset attribution should identify the NYC BUILDING dataset.

Valhalla is open-source software. The public demo is a shared research service with fair-use expectations, not a production service-level agreement. Requests should be throttled and identify the client as required by the hosting operator.

## Provider policy references

- [Overpass API usage guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)
- [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
- [OSM attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)
- [Valhalla routing API](https://valhalla.github.io/valhalla/api/)
- [Valhalla public demo information](https://github.com/valhalla/valhalla#demo-server)
- [NYC BUILDING dataset](https://data.cityofnewyork.us/City-Government/BUILDING/5zhs-2jue)
- [NYC OTI building-footprints metadata](https://github.com/CityOfNewYork/nyc-geo-metadata/blob/main/Metadata/Metadata_BuildingFootprints.md)
- [Socrata API endpoints](https://dev.socrata.com/docs/endpoints)
- [Socrata spatial query functions](https://dev.socrata.com/docs/functions/within_circle)
- [NYC Open Data Terms of Use](https://opendata.cityofnewyork.us/overview/#terms-of-use)
