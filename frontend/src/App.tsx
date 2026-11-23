import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Landmark,
  RecommendationResponse,
  ScoredRoute,
} from './types';
import { fetchLandmarks, getRecommendation } from './services/api';
import { Header } from './components/Header';
import { JourneySelector } from './components/JourneySelector';
import { PreferenceInput } from './components/PreferenceInput';
import { Map } from './components/Map';
import { ErrorBanner } from './components/ErrorBanner';

const formatDistance = (distance: number) =>
  distance >= 1000
    ? `${(distance / 1000).toFixed(2)} km`
    : `${Math.round(distance)} m`;

export const App: React.FC = () => {
  const [landmarks, setLandmarks] = useState<Landmark[]>([]);
  const [originId, setOriginId] = useState('grand_central_terminal');
  const [destinationId, setDestinationId] = useState('bryant_park');
  const [preferenceText, setPreferenceText] = useState('');

  const [recommendation, setRecommendation] =
    useState<RecommendationResponse | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);

  const [isLoadingLandmarks, setIsLoadingLandmarks] = useState(true);
  const [isLoadingRecommend, setIsLoadingRecommend] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const requestCounter = useRef(0);

  useEffect(() => {
    let isMounted = true;

    fetchLandmarks()
      .then((data) => {
        if (isMounted) setLandmarks(data.landmarks);
      })
      .catch((error: unknown) => {
        if (!isMounted) return;
        setErrorMessage(
          error instanceof Error ? error.message : 'Failed to load landmarks.'
        );
      })
      .finally(() => {
        if (isMounted) setIsLoadingLandmarks(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const loadJourneyData = useCallback(
    async (origin: string, destination: string, preference: string) => {
      const requestId = ++requestCounter.current;
      setErrorMessage(null);
      setIsLoadingRecommend(true);
      setRecommendation(null);
      setSelectedRouteId(null);

      try {
        const result = await getRecommendation(origin, destination, preference);
        if (requestId !== requestCounter.current) return;
        setRecommendation(result);
        setSelectedRouteId(result.recommended_route.route_id);
      } catch (error: unknown) {
        if (requestId !== requestCounter.current) return;
        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'Unable to generate walking route recommendations.'
        );
      } finally {
        if (requestId === requestCounter.current) setIsLoadingRecommend(false);
      }
    },
    []
  );

  const handleApplyPreference = (newPreference: string) => {
    setPreferenceText(newPreference);
    void loadJourneyData(originId, destinationId, newPreference);
  };

  // Changing the journey discards the old result; a new route is only
  // calculated when the user presses "Update route".
  const clearRecommendation = () => {
    requestCounter.current += 1;
    setIsLoadingRecommend(false);
    setRecommendation(null);
    setSelectedRouteId(null);
    setErrorMessage(null);
  };

  const handleOriginChange = (id: string) => {
    setOriginId(id);
    clearRecommendation();
  };

  const handleDestinationChange = (id: string) => {
    setDestinationId(id);
    clearRecommendation();
  };

  const handleSwap = () => {
    setOriginId(destinationId);
    setDestinationId(originId);
    clearRecommendation();
  };

  const handleReset = () => {
    setOriginId('grand_central_terminal');
    setDestinationId('bryant_park');
    clearRecommendation();
  };

  const originName = landmarks.find((l) => l.id === originId)?.name;
  const destinationName = landmarks.find((l) => l.id === destinationId)?.name;

  const candidateRoutes = recommendation?.candidate_routes ?? [];

  const selectedRoute =
    candidateRoutes.find((route) => route.route_id === selectedRouteId) ??
    candidateRoutes.find((route) => route.recommended) ??
    candidateRoutes[0] ??
    null;

  const scoreFor = (routeId: string): ScoredRoute | undefined =>
    !recommendation
      ? undefined
      : recommendation.ranked_alternatives.find((r) => r.route_id === routeId) ??
        (recommendation.recommended_route.route_id === routeId
          ? recommendation.recommended_route
          : undefined);

  const selectedScore = selectedRoute ? scoreFor(selectedRoute.route_id) : undefined;
  const busy = isLoadingRecommend || isLoadingLandmarks;

  const stops = selectedScore?.landmarks_visited?.length
    ? selectedScore.landmarks_visited.map((l) => ({
        id: l.landmark_id,
        name: l.landmark_name,
        note: l.description,
      }))
    : (selectedRoute?.via_waypoints ?? []).map((w) => ({
        id: w.landmark_id,
        name: w.landmark_name,
        note: w.note ?? '',
      }));

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col font-sans">
      <Header />

      <main className="flex-1 max-w-[1400px] w-full mx-auto px-4 sm:px-6 py-4 grid grid-cols-1 lg:grid-cols-[380px,1fr] gap-4 items-start">
        {/* Left column: plan + results */}
        <div className="space-y-4 min-w-0">
          <section className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 space-y-4">
            <JourneySelector
              landmarks={landmarks}
              originId={originId}
              destinationId={destinationId}
              onOriginChange={handleOriginChange}
              onDestinationChange={handleDestinationChange}
              onSwap={handleSwap}
              isLoading={busy}
            />
            <PreferenceInput
              currentPreference={preferenceText}
              onApplyPreference={handleApplyPreference}
              isLoading={busy}
            />
          </section>

          {errorMessage && (
            <ErrorBanner
              message={errorMessage}
              onRetry={() =>
                loadJourneyData(originId, destinationId, preferenceText)
              }
              onResetToCore={handleReset}
            />
          )}

          {isLoadingRecommend && (
            <div className="space-y-2" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-[72px] rounded-xl bg-white border border-slate-200 animate-pulse"
                />
              ))}
            </div>
          )}

          {!isLoadingRecommend && recommendation && selectedRoute && (
            <>
              <section aria-label="Route options">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2 px-1">
                  {candidateRoutes.length} route option
                  {candidateRoutes.length === 1 ? '' : 's'}
                </h2>
                <div className="space-y-2">
                  {candidateRoutes.map((route, index) => {
                    const isSelected = route.route_id === selectedRoute.route_id;
                    const stopCount = route.via_waypoints.length;
                    return (
                      <button
                        key={route.route_id}
                        type="button"
                        onClick={() => setSelectedRouteId(route.route_id)}
                        aria-pressed={isSelected}
                        className={`w-full text-left rounded-xl border px-4 py-3 flex items-center justify-between gap-3 ${
                          isSelected
                            ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-400'
                            : 'border-slate-200 bg-white hover:border-indigo-300'
                        }`}
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                            {route.estimated_duration_minutes} min
                            <span className="font-normal text-slate-500">
                              {formatDistance(route.distance_m)}
                            </span>
                          </p>
                          <p className="text-xs text-slate-500 mt-0.5">
                            Route {route.rank ?? index + 1} &bull;{' '}
                            {stopCount === 0
                              ? 'direct'
                              : `${stopCount} stop${stopCount === 1 ? '' : 's'}`}
                          </p>
                        </div>
                        {route.recommended && (
                          <span className="shrink-0 text-[11px] font-semibold rounded-full bg-emerald-100 text-emerald-800 px-2 py-0.5">
                            Best match
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </section>

              <section className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                <h2 className="text-sm font-semibold text-slate-900">
                  {selectedRoute.recommended
                    ? 'Why this route'
                    : 'About this alternative'}
                </h2>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
                  {selectedRoute.recommended
                    ? recommendation.explanation
                    : `A ${selectedRoute.estimated_duration_minutes}-minute, ${formatDistance(
                        selectedRoute.distance_m
                      )} walk. Select the "Best match" route to see why Jaunt recommended it.`}
                </p>

                <h2 className="text-sm font-semibold text-slate-900 mt-4">
                  Along the way
                </h2>
                {stops.length > 0 ? (
                  <ol className="mt-2 space-y-2.5">
                    {stops.map((stop, i) => (
                      <li key={stop.id} className="flex gap-2.5">
                        <span className="mt-0.5 w-5 h-5 shrink-0 rounded-full bg-indigo-100 text-indigo-700 text-[11px] font-bold flex items-center justify-center">
                          {i + 1}
                        </span>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-slate-900">
                            {stop.name}
                          </p>
                          {stop.note && (
                            <p className="text-xs text-slate-500 mt-0.5">
                              {stop.note}
                            </p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-sm text-slate-500 mt-1.5">
                    A direct walk with no intermediate stops.
                  </p>
                )}

                <details className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-500">
                  <summary className="cursor-pointer select-none font-medium text-slate-600">
                    How was this chosen?
                  </summary>
                  <div className="mt-2 space-y-1">
                    <p>
                      Preference matching:{' '}
                      {recommendation.ai_status?.status === 'active'
                        ? 'AI-assisted'
                        : 'built-in matching'}
                    </p>
                    {recommendation.ai_status?.message && (
                      <p>{recommendation.ai_status.message}</p>
                    )}
                  </div>
                </details>
              </section>
            </>
          )}
        </div>

        {/* Right column: map */}
        <div className="lg:sticky lg:top-4 min-w-0">
          <div className="relative bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden lg:h-[calc(100vh-6.5rem)]">
            <Map
              routes={!isLoadingRecommend && selectedRoute ? [selectedRoute] : []}
              originName={originName}
              destinationName={destinationName}
            />

            {!isLoadingRecommend && selectedRoute && (
              <div className="absolute top-3 left-3 right-3 sm:right-auto z-[1000] bg-white/95 backdrop-blur border border-slate-200 rounded-lg shadow-md px-4 py-2 flex items-center gap-4 text-sm">
                <span className="font-semibold text-slate-900">
                  {selectedRoute.estimated_duration_minutes} min
                </span>
                <span className="text-slate-600">
                  {formatDistance(selectedRoute.distance_m)}
                </span>
                <span className="text-slate-500 truncate">
                  {originName} &rarr; {destinationName}
                </span>
              </div>
            )}

            {!isLoadingRecommend && !selectedRoute && !errorMessage && (
              <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-white/70 pointer-events-none p-6">
                <p className="max-w-xs text-center text-sm font-medium text-slate-700 bg-white rounded-lg border border-slate-200 shadow-sm px-4 py-3">
                  Pick a start and destination, describe your walk, then press
                  <strong> Update route</strong>.
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
};

export default App;
