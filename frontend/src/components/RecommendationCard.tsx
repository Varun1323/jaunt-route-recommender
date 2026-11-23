import React, { useState } from 'react';
import { RecommendationResponse, RouteItem } from '../types';

interface RecommendationCardProps {
  recommendation: RecommendationResponse;
  routes: RouteItem[];
  selectedRouteId: string;
  onSelectRoute: (routeId: string) => void;
}

export const RecommendationCard: React.FC<RecommendationCardProps> = ({
  recommendation,
  selectedRouteId,
  onSelectRoute,
}) => {
  const [showScoreBreakdown, setShowScoreBreakdown] = useState(false);
  const winner = recommendation.recommended_route;
  const isSelected = selectedRouteId === winner.route_id;
  const unmetConstraints = Object.entries(winner.constraint_status ?? {})
    .filter(([, satisfied]) => !satisfied)
    .map(([constraint]) => constraint.replace(/_/g, ' '));

  return (
    <div className={`rounded-xl border transition-all ${
      isSelected
        ? 'bg-gradient-to-br from-indigo-50/90 via-white to-slate-50 border-indigo-400 shadow-md ring-2 ring-indigo-500/20'
        : 'bg-white border-slate-200 shadow-sm hover:border-indigo-300'
    } p-5`}>
      {/* Badge Header */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-indigo-600 text-white shadow-xs">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
            </svg>
            Recommended Choice
          </span>
          <span className="text-xs font-semibold text-slate-500">
            Score: <span className="text-indigo-700 font-bold">{(winner.score * 100).toFixed(0)}%</span>
          </span>
        </div>

        <button
          type="button"
          onClick={() => onSelectRoute(winner.route_id)}
          className={`text-xs px-3 py-1 rounded-md font-medium transition-all ${
            isSelected
              ? 'bg-indigo-600 text-white shadow-xs'
              : 'bg-slate-100 hover:bg-indigo-50 hover:text-indigo-600 text-slate-700'
          }`}
        >
          {isSelected ? 'Viewing on Map' : 'Show on Map'}
        </button>
      </div>

      {/* Title */}
      <h3 className="text-lg font-bold text-slate-900 mb-1 flex items-center gap-2">
        {winner.route_name}
      </h3>

      {/* Metrics Row */}
      <div className="flex flex-wrap items-center gap-4 py-2 text-sm text-slate-700 border-b border-slate-100 mb-3">
        <div className="flex items-center gap-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-indigo-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span className="font-bold text-slate-900">{winner.estimated_duration_minutes}</span> min
          <span className="text-xs text-slate-400">({winner.estimated_duration_s.toFixed(0)}s)</span>
        </div>

        <div className="flex items-center gap-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-indigo-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
          <span className="font-bold text-slate-900">{winner.distance_m.toFixed(0)}</span> metres
        </div>

        <div className="flex items-center gap-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-amber-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 21h18M3 10h18M5 6l7-3 7 3M4 10v11M20 10v11M8 14v4M12 14v4M16 14v4" />
          </svg>
          <span className="font-bold text-slate-900">{winner.landmark_count}</span>
          <span>interesting place{winner.landmark_count === 1 ? '' : 's'} along or near route</span>
        </div>
      </div>

      {/* Rationale Explanation */}
      <div className="bg-indigo-50/70 border border-indigo-100 rounded-lg p-3 text-xs text-indigo-950 leading-relaxed mb-4">
        <span className="font-bold text-indigo-900 block mb-1 flex items-center gap-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-indigo-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          Recommendation Rationale:
        </span>
        <p>{recommendation.explanation}</p>
      </div>

      {recommendation.ai_status && (
        <p
          role="status"
          className={`rounded-md px-3 py-2 mb-4 text-xs ${
            recommendation.ai_status.status === 'active'
              ? 'bg-emerald-50 text-emerald-800'
              : recommendation.ai_status.status === 'unconfigured'
                ? 'bg-slate-100 text-slate-600'
                : 'bg-amber-50 text-amber-800'
          }`}
        >
          {recommendation.ai_status.message}
        </p>
      )}

      {recommendation.route_generation && (
        <p
          aria-live="polite"
          className={`rounded-md px-3 py-2 mb-4 text-xs ${
            recommendation.route_generation.status === 'generated'
              ? 'bg-sky-50 text-sky-800'
              : ['generation_failed', 'generated_with_errors'].includes(recommendation.route_generation.status)
                ? 'bg-amber-50 text-amber-800'
                : 'bg-slate-100 text-slate-600'
          }`}
        >
          Route strategy: {recommendation.route_generation.strategy}.{' '}
          {recommendation.route_generation.reason}
        </p>
      )}

      {recommendation.parsed_preferences.unsupported_preferences.length > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 mb-4 text-xs text-amber-800">
          Not scored because route data is unavailable: {
            recommendation.parsed_preferences.unsupported_preferences.join(', ')
          }.
        </p>
      )}

      {!winner.is_feasible && (
        <p role="status" className="rounded-md bg-amber-50 px-3 py-2 mb-4 text-xs text-amber-800">
          No compared route satisfies every hard constraint. This is the best available candidate.
          {unmetConstraints.length > 0 && ` Unmet: ${unmetConstraints.join(', ')}.`}
        </p>
      )}

      {/* Landmarks Visited Section */}
      {winner.landmarks_visited && winner.landmarks_visited.length > 0 && (
        <div className="mb-3">
          <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z"/>
            </svg>
            Interesting Places Along or Near This Route ({winner.landmarks_visited.length}):
          </h4>
          <div className="space-y-2">
            {winner.landmarks_visited.map((lm) => (
              <div
                key={lm.landmark_id}
                className="bg-slate-50 border border-slate-200/80 rounded-lg p-2.5 text-xs text-slate-700 hover:bg-slate-100/70 transition-colors"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="font-bold text-slate-900">{lm.landmark_name}</span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {lm.source === 'via_waypoint' ? (
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                        Via Stop
                      </span>
                    ) : (
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200" title={`Within ${lm.min_distance_m?.toFixed(0)}m of route`}>
                        Nearby ({lm.min_distance_m?.toFixed(0)}m)
                      </span>
                    )}
                  </div>
                </div>

                {lm.architectural_relevance ? (
                  <p className="text-[11px] text-slate-600 line-clamp-2">{lm.architectural_relevance}</p>
                ) : (
                  <p className="text-[11px] text-slate-500 line-clamp-2">{lm.description}</p>
                )}

                {lm.categories && lm.categories.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {lm.categories.map((c) => (
                      <span key={c} className="text-[10px] px-1.5 py-0.2 rounded bg-slate-200/70 text-slate-600">
                        {c.replace(/_/g, ' ')}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Collapsible Score Breakdown */}
      <div className="pt-2 border-t border-slate-100">
        <button
          type="button"
          onClick={() => setShowScoreBreakdown(!showScoreBreakdown)}
          className="text-[11px] font-semibold text-indigo-600 hover:text-indigo-800 flex items-center gap-1 transition-colors"
        >
          <span>{showScoreBreakdown ? 'Hide' : 'Show'} Preference Scoring Breakdown</span>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className={`w-3.5 h-3.5 transform transition-transform ${showScoreBreakdown ? 'rotate-180' : ''}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>

        {showScoreBreakdown && winner.score_breakdown && (
          <div className="mt-2.5 p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs space-y-2">
            <div>
              <div className="flex justify-between text-slate-600 mb-1">
                <span>Distance Efficiency</span>
                <span className="font-semibold text-slate-900">{((winner.score_breakdown.distance_efficiency ?? 0) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${(winner.score_breakdown.distance_efficiency ?? 0) * 100}%` }} />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-slate-600 mb-1">
                <span>Time Efficiency</span>
                <span className="font-semibold text-slate-900">{((winner.score_breakdown.time_efficiency ?? 0) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${(winner.score_breakdown.time_efficiency ?? 0) * 100}%` }} />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-slate-600 mb-1">
                <span>Architecture Relevance</span>
                <span className="font-semibold text-slate-900">{((winner.score_breakdown.architecture_relevance ?? 0) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${(winner.score_breakdown.architecture_relevance ?? 0) * 100}%` }} />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-slate-600 mb-1">
                <span>Landmark Quantity</span>
                <span className="font-semibold text-slate-900">{((winner.score_breakdown.landmarks_quantity ?? 0) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${(winner.score_breakdown.landmarks_quantity ?? 0) * 100}%` }} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
