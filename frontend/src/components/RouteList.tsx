import React from 'react';
import { RouteItem, ScoredRoute } from '../types';

interface RouteListProps {
  routes: RouteItem[];
  recommendedRouteId?: string;
  scoredRoutes: ScoredRoute[];
  selectedRouteId: string;
  onSelectRoute: (routeId: string) => void;
}

export const RouteList: React.FC<RouteListProps> = ({
  routes,
  recommendedRouteId,
  scoredRoutes,
  selectedRouteId,
  onSelectRoute,
}) => {
  if (!routes || routes.length === 0) {
    return null;
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-indigo-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
          Compared Genuine Pedestrian Candidates ({routes.length})
        </h3>
        <span className="text-[11px] text-slate-400">Valhalla Pedestrian Costing</span>
      </div>

      <div className="space-y-2.5">
        {routes.map((route) => {
          const isRecommended = route.route_id === recommendedRouteId;
          const isSelected = route.route_id === selectedRouteId;
          const scoredRoute = scoredRoutes.find((candidate) => candidate.route_id === route.route_id);

          return (
            <div
              key={route.route_id}
              onClick={() => onSelectRoute(route.route_id)}
              className={`p-3 rounded-lg border cursor-pointer transition-all ${
                isSelected
                  ? 'border-indigo-500 bg-indigo-50/50 shadow-xs ring-1 ring-indigo-500'
                  : 'border-slate-200 bg-slate-50/60 hover:bg-slate-100/70 hover:border-slate-300'
              }`}
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-2">
                  <span
                    className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                      isRecommended
                        ? 'bg-indigo-600 ring-2 ring-indigo-200'
                        : isSelected
                        ? 'bg-indigo-400'
                        : 'bg-slate-400'
                    }`}
                  />
                  <h4 className="font-bold text-xs text-slate-900 leading-snug">
                    {route.route_name}
                  </h4>
                </div>

                {isRecommended && (
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-indigo-600 text-white shrink-0">
                    Recommended
                  </span>
                )}
              </div>

              {route.route_description && (
                <p className="text-[11px] text-slate-500 line-clamp-1 mb-2 ml-4.5">
                  {route.route_description}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600 ml-4.5">
                <span className="flex items-center gap-1 font-medium">
                  <span className="font-bold text-slate-900">{route.estimated_duration_minutes}</span> min
                </span>
                <span className="text-slate-300">&bull;</span>
                <span className="flex items-center gap-1 font-medium">
                  <span className="font-bold text-slate-900">{route.distance_m.toFixed(0)}</span> m
                </span>

                {route.via_waypoints && route.via_waypoints.length > 0 && (
                  <>
                    <span className="text-slate-300">&bull;</span>
                    <span className="text-[11px] text-purple-700 font-medium">
                      via {route.via_waypoints.map((w) => w.landmark_name).join(', ')}
                    </span>
                  </>
                )}
              </div>

              {scoredRoute?.landmarks_visited.length ? (
                <p className="mt-2 ml-4.5 text-[11px] text-slate-600">
                  Places: {scoredRoute.landmarks_visited.map((place) =>
                    `${place.landmark_name}${place.source === 'via_waypoint' ? ' (stop)' : ' (nearby)'}`
                  ).join(', ')}
                </p>
              ) : null}

              {scoredRoute?.score_breakdown && (
                <p className="mt-1 ml-4.5 text-[10px] text-slate-500">
                  {(() => {
                    const extraDistance = scoredRoute.score_breakdown.extra_distance_vs_shortest_m ?? 0;
                    const extraTime = (scoredRoute.score_breakdown.extra_time_vs_fastest_s ?? 0) / 60;
                    return `Distance vs shortest: ${extraDistance >= 0 ? '+' : ''}${extraDistance.toFixed(0)} m; time vs fastest: ${extraTime >= 0 ? '+' : ''}${extraTime.toFixed(1)} min.`;
                  })()}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
