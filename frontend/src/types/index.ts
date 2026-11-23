export interface Landmark {
  id: string;
  name: string;
  street_address?: string;
  description: string;
  categories: string[];
  architectural_relevance?: string;
  historical_relevance?: string;
  access_type?: string;
  source_urls?: string[];
  coordinate_verification_status: string;
  prototype_selection?: boolean;
  prototype_display_name?: string;
  alternate_names?: string[];
}

export interface LandmarksResponse {
  count: number;
  landmarks: Landmark[];
  limitation_disclosure: string;
}

export interface GeoJsonGeometry {
  type: 'LineString';
  coordinates: [number, number][]; // [lon, lat]
}

export interface GeoJsonFeature {
  type: 'Feature';
  geometry: GeoJsonGeometry;
  properties: Record<string, unknown>;
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection';
  name?: string;
  features: GeoJsonFeature[];
  metadata?: Record<string, unknown>;
}

export interface ViaWaypoint {
  landmark_id: string;
  landmark_name: string;
  note?: string;
  lat?: number;
  lon?: number;
}

export interface RouteItem {
  route_id: string;
  route_name: string;
  route_description?: string;
  distance_m: number;
  estimated_duration_s: number;
  estimated_duration_minutes: number;
  via_waypoints: ViaWaypoint[];
  verification_status: string;
  geojson: GeoJsonFeatureCollection;
}

export interface RoutesResponse {
  origin_id: string;
  destination_id: string;
  route_count: number;
  routes: RouteItem[];
  limitation_disclosure: string;
}

export interface LandmarkVisited {
  landmark_id: string;
  landmark_name: string;
  categories: string[];
  description: string;
  architectural_relevance: string;
  source: 'via_waypoint' | 'nearby_route_geometry';
  min_distance_m?: number;
  coordinate_note?: string;
}

export interface ScoredRoute {
  route_id: string;
  route_name: string;
  distance_m: number;
  estimated_duration_s: number;
  estimated_duration_minutes: number;
  landmarks_visited: LandmarkVisited[];
  landmark_count: number;
  is_feasible: boolean;
  score: number;
  score_breakdown: {
    base_score?: number;
    distance_efficiency?: number;
    time_efficiency?: number;
    landmarks_quantity?: number;
    architecture_relevance?: number;
    history_relevance?: number;
    time_penalty_factor?: number;
    preferred_category_relevance?: number;
    preferred_landmark_relevance?: number;
    detour_ratio_vs_shortest?: number;
    extra_distance_vs_shortest_m?: number;
    extra_time_vs_fastest_s?: number;
    distance_penalty_factor?: number;
    walking_time_budget_minutes?: number | null;
    budget_penalty_factor?: number;
    budget_feasible?: boolean | null;
  };
  constraint_status?: Record<string, boolean>;
}

export interface ParsedPreferences {
  architecture: number;
  history: number;
  shortest_distance: number;
  minimum_walking_time: number;
  more_landmarks: number;
  min_landmarks: number;
  walking_time_budget_minutes?: number | null;
  max_duration_minutes: number | null;
  max_distance_m?: number | null;
  max_detour_ratio: number | null;
  excluded_categories: string[];
  preferred_categories?: string[];
  primary_interest?: string | null;
  secondary_interests?: string[];
  requested_landmark_ids?: string[];
  preferred_landmark_ids?: string[];
  route_priority?: 'efficiency' | 'sightseeing' | 'balanced';
  is_qualitative_time_constrained: boolean;
  raw_query: string;
  unsupported_preferences: string[];
}

export interface RecommendationResponse {
  query: string;
  origin_landmark_id: string;
  origin_landmark_name: string;
  destination_landmark_id: string;
  destination_landmark_name: string;
  parsed_preferences: ParsedPreferences;
  recommended_route: ScoredRoute;
  ranked_alternatives: ScoredRoute[];
  candidate_routes: RouteItem[];
  individual_scores: Record<string, number>;
  estimated_walking_time_s: number;
  estimated_walking_time_minutes: number;
  distance_m: number;
  landmarks_visited: string[];
  explanation: string;
  limitation_disclosure: string;
  ai_status?: {
    provider: string;
    status: 'active' | 'partial' | 'fallback' | 'unconfigured';
    message: string;
  };
  route_generation?: {
    strategy: 'none' | 'landmark' | 'native' | 'both';
    status: 'reused' | 'generated' | 'generated_with_errors' | 'no_meaningful_alternative' | 'generation_failed' | 'disabled';
    reason: string;
    generated_route_ids: string[];
    reused_route_count: number;
    available_route_count: number;
    failure_count: number;
  };
}
export interface V1RouteSummary {
  route_id: string;
  origin_landmark_id: string;
  origin_landmark_name: string;
  destination_landmark_id: string;
  destination_landmark_name: string;
  distance_m: number;
  estimated_duration_s: number;
  estimated_duration_minutes: number;
  via_waypoints: Array<{
    landmark_id: string;
    landmark_name: string;
    lat?: number;
    lon?: number;
    note?: string;
  }>;
  costing: string;
  verification_status: string;
  coordinate_limitation_disclosure: string;
}

export interface V1PreferenceResult {
  waypoint_ids: string[];
  primary_preference: string;
  detour_tolerance: 'low' | 'moderate' | 'high';
  reasoning: string;
}

export interface V1RecommendationResponse {
  route: GeoJsonFeatureCollection;
  route_summary: V1RouteSummary;
  preference: V1PreferenceResult;
  ai_status: {
    provider: string;
    model: string;
    active: boolean;
  };
  route_count: 1;
}
export interface RouteItem {
  route_id: string;
  route_name: string;
  route_description?: string;
  distance_m: number;
  estimated_duration_s: number;
  estimated_duration_minutes: number;
  via_waypoints: ViaWaypoint[];
  verification_status: string;
  geojson: GeoJsonFeatureCollection;
  recommended?: boolean;
  rank?: number | null;
}