import { expect, test, type Page, type Response } from '@playwright/test';

const API_ORIGIN = 'http://127.0.0.1:8000';
const originSelect = (page: Page) => page.getByLabel('Start landmark');
const destinationSelect = (page: Page) => page.getByLabel('Destination landmark');
const routeMap = (page: Page) => page.locator('.leaflet-container');
const mapPaths = (page: Page) => page.locator('.leaflet-overlay-pane path.leaflet-interactive');
const mapMarkers = (page: Page) => page.locator('.leaflet-marker-icon.custom-map-pin');

async function waitForJourneyResponse(
  page: Page,
  endpoint: 'routes' | 'recommend',
  originId: string,
  destinationId: string,
): Promise<Response> {
  return page.waitForResponse((response) => {
    const request = response.request();
    if (!response.url().includes(`/api/${endpoint}`)) return false;
    if (endpoint === 'routes') {
      const url = new URL(response.url());
      return url.searchParams.get('origin_id') === originId &&
        url.searchParams.get('destination_id') === destinationId;
    }
    if (request.method() !== 'POST') return false;
    const body = request.postDataJSON() as { origin_id?: string; destination_id?: string };
    return body.origin_id === originId && body.destination_id === destinationId;
  });
}

async function waitForJourney(page: Page, originId: string, destinationId: string) {
  const routesResponse = await waitForJourneyResponse(page, 'routes', originId, destinationId);
  expect(routesResponse.status(), `GET routes for ${originId} -> ${destinationId}`).toBe(200);
  const recommendationResponse = await waitForJourneyResponse(page, 'recommend', originId, destinationId);
  expect(recommendationResponse.status(), `POST recommendation for ${originId} -> ${destinationId}`).toBe(200);
  await expect(page.getByText('Fetching genuine pedestrian routes...', { exact: true })).toBeHidden();
  await expect(routeMap(page)).toBeVisible();
  return {
    routes: await routesResponse.json(),
    recommendation: await recommendationResponse.json(),
  };
}

async function selectJourney(
  page: Page,
  targetOrigin: string,
  targetDestination: string,
): Promise<{ routes: any; recommendation: any }> {
  let currentOrigin = await originSelect(page).inputValue();
  let currentDestination = await destinationSelect(page).inputValue();
  if (currentOrigin === targetOrigin && currentDestination === targetDestination) {
    const [routesResponse, recommendationResponse] = await Promise.all([
      page.request.get(`${API_ORIGIN}/api/routes`, { params: { origin_id: targetOrigin, destination_id: targetDestination } }),
      page.request.post(`${API_ORIGIN}/api/recommend`, {
        data: {
          origin_id: targetOrigin,
          destination_id: targetDestination,
          preference: await page.getByLabel('What kind of walk do you want?').inputValue(),
        },
      }),
    ]);
    return { routes: await routesResponse.json(), recommendation: await recommendationResponse.json() };
  }

  if (targetOrigin === currentDestination && targetDestination === currentOrigin) {
    const responsePromise = waitForJourney(page, targetOrigin, targetDestination);
    await page.getByRole('button', { name: 'Swap Origin and Destination' }).click();
    return responsePromise;
  }

  if (targetOrigin === currentDestination) {
    // Move the destination first to avoid selecting the currently disabled origin value.
    const interimDestination = targetDestination;
    const responsePromise = waitForJourney(page, currentOrigin, interimDestination);
    await destinationSelect(page).selectOption(interimDestination);
    await responsePromise;
    currentDestination = interimDestination;
    const finalResponsePromise = waitForJourney(page, targetOrigin, targetDestination);
    await originSelect(page).selectOption(targetOrigin);
    return finalResponsePromise;
  }

  if (targetOrigin !== currentOrigin) {
    const intermediatePairIsFinal = currentDestination === targetDestination;
    const responsePromise = waitForJourney(page, targetOrigin, currentDestination);
    await originSelect(page).selectOption(targetOrigin);
    const result = await responsePromise;
    currentOrigin = targetOrigin;
    if (intermediatePairIsFinal) return result;
  }

  if (currentDestination !== targetDestination) {
    const responsePromise = waitForJourney(page, currentOrigin, targetDestination);
    await destinationSelect(page).selectOption(targetDestination);
    return responsePromise;
  }

  throw new Error(
    `Unable to select journey: ${targetOrigin} -> ${targetDestination}`,
  );
}

async function blockMapTiles(page: Page) {
  await page.route('https://*.tile.openstreetmap.org/**', (route) => route.abort());
  await page.route('https://tile.openstreetmap.org/**', (route) => route.abort());
}

test.beforeEach(async ({ page }) => {
  await blockMapTiles(page);

  await page.goto('/');

  await expect(
    page.getByRole('heading', { name: 'Jaunt' }),
  ).toBeVisible();

  await expect(
    originSelect(page),
  ).toBeEnabled();

  // Generate the initial recommendation through the actual UI.
  const recommendationResponse = page.waitForResponse((response) => {
    return (
      response.url().includes('/api/recommend') &&
      response.request().method() === 'POST'
    );
  });

  await page.getByRole('button', { name: 'Update route' }).click();

  expect((await recommendationResponse).status()).toBe(200);

  // The map is rendered after a recommendation is available.
  await expect(routeMap(page)).toBeVisible();
});

test('loads eight selectable landmarks, route geometry, metrics, markers, and limitation disclosure', async ({ page }) => {
  const origin = originSelect(page);
  const destination = destinationSelect(page);
  await expect(origin.locator('option')).toHaveCount(8);
  await expect(destination.locator('option')).toHaveCount(8);

  const landmarksResponse = await page.request.get(`${API_ORIGIN}/api/landmarks`);
  expect(landmarksResponse.status()).toBe(200);
  const landmarksData = await landmarksResponse.json();
  const landmarkNames = new Map<string, string>(landmarksData.landmarks.map((landmark: { id: string; name: string }) => [landmark.id, landmark.name]));
  const expectedLandmarkIds = landmarksData.landmarks.map((landmark: { id: string }) => landmark.id).sort();
  expect((await origin.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).sort()).toEqual(expectedLandmarkIds);
  expect((await destination.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).sort()).toEqual(expectedLandmarkIds);

  const routesResponse = await page.request.get(`${API_ORIGIN}/api/routes`, {
    params: { origin_id: 'grand_central_terminal', destination_id: 'bryant_park' },
  });
  expect(routesResponse.status()).toBe(200);
  const routesData = await routesResponse.json();
  expect(routesData.route_count).toBeGreaterThan(0);
  for (const route of routesData.routes) {
    const feature = route.geojson.features[0];
    expect(feature.geometry.type).toBe('LineString');
    expect(feature.geometry.coordinates.length).toBeGreaterThan(1);
    for (const [longitude, latitude] of feature.geometry.coordinates) {
      expect(longitude).toBeGreaterThanOrEqual(-180);
      expect(longitude).toBeLessThanOrEqual(180);
      expect(latitude).toBeGreaterThanOrEqual(-90);
      expect(latitude).toBeLessThanOrEqual(90);
    }
  }

  await expect(page.getByRole('heading', { name: `Available Genuine Pedestrian Routes (${routesData.route_count})` })).toBeVisible();
  await expect(mapPaths(page)).toHaveCount(routesData.route_count + 1); // selected route has a two-layer highlight
  await expect(mapMarkers(page)).toHaveCount(2);
  const recommendationTitle = page.locator('h3').filter({ hasText: 'Route' }).first();
  await expect(recommendationTitle).toBeVisible();
  const currentPreference = await page.getByLabel('What kind of walk do you want?').inputValue();
  const initialRecommendationResponse = await page.request.post(`${API_ORIGIN}/api/recommend`, {
    data: {
      origin_id: 'grand_central_terminal',
      destination_id: 'bryant_park',
      preference: currentPreference,
    },
  });
  const initialRecommendation = await initialRecommendationResponse.json();
  const recommendationHeading = page.getByRole('heading', {
    level: 3,
    name: initialRecommendation.recommended_route.route_name,
    exact: true,
  });
  await expect(recommendationHeading).toBeVisible();
  const recommendationCard = recommendationHeading.locator('..');
  await expect(recommendationCard).toContainText(String(Math.round(initialRecommendation.distance_m)));
  await expect(recommendationCard).toContainText(`${initialRecommendation.recommended_route.estimated_duration_minutes} min`);
  await expect(page.getByRole('status')).toContainText('OpenRouter API key is not configured');

  const selectedMarker = mapMarkers(page).first();
  await selectedMarker.click();
  await expect(page.locator('.leaflet-popup-content')).toContainText(landmarkNames.get('grand_central_terminal')!);
  await page.keyboard.press('Escape');
  await mapMarkers(page).last().click();
  await expect(page.locator('.leaflet-popup-content').last()).toContainText(landmarkNames.get('bryant_park')!);

  const boundsAudit = await page.evaluate(() => {
    const map = document.querySelector('.leaflet-container')!.getBoundingClientRect();
    const markers = Array.from(document.querySelectorAll('.leaflet-marker-icon.custom-map-pin'));
    const routePaths = Array.from(document.querySelectorAll('.leaflet-overlay-pane path.leaflet-interactive'));
    return {
      markersInside: markers.every((marker) => {
        const rect = marker.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        return x >= map.left && x <= map.right && y >= map.top && y <= map.bottom;
      }),
      routePathCount: routePaths.length,
      visibleRoutePathCount: routePaths.filter((path) => {
        const rect = path.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.right >= map.left && rect.left <= map.right && rect.bottom >= map.top && rect.top <= map.bottom;
      }).length,
    };
  });
  expect(boundsAudit.markersInside).toBe(true);
  expect(boundsAudit.visibleRoutePathCount).toBeGreaterThanOrEqual(routesData.route_count);
});

test('loads cached pedestrian routes for all 56 directed journeys in the selectors', async ({ page }) => {
  const landmarksResponse = await page.request.get(`${API_ORIGIN}/api/landmarks`);
  const landmarks = (await landmarksResponse.json()).landmarks as Array<{ id: string }>;
  let testedJourneys = 0;

  for (const origin of landmarks) {
    for (const destination of landmarks) {
      if (origin.id === destination.id) continue;
      const result = await selectJourney(page, origin.id, destination.id);
      expect(result.routes.origin_id).toBe(origin.id);
      expect(result.routes.destination_id).toBe(destination.id);
      expect(result.routes.route_count).toBeGreaterThan(0);
      expect(result.recommendation.origin_landmark_id).toBe(origin.id);
      expect(result.recommendation.destination_landmark_id).toBe(destination.id);
      expect(result.recommendation.distance_m).toBe(result.recommendation.recommended_route.distance_m);
      expect(result.recommendation.estimated_walking_time_s).toBe(result.recommendation.recommended_route.estimated_duration_s);
      const routeCards = page.getByText(/Available Genuine Pedestrian Routes \(/).first();
      await expect(routeCards).toContainText(String(result.routes.route_count));
      await expect(mapMarkers(page)).toHaveCount(2);
      const lineCount = await mapPaths(page).count();
      expect(lineCount).toBeGreaterThanOrEqual(result.routes.route_count);
      for (const route of result.routes.routes) {
        expect(route.verification_status).toBe('prototype_candidate_unverified');
        expect(route.geojson.features[0].geometry.type).toBe('LineString');
        expect(route.geojson.features[0].properties.costing).toBe('pedestrian');
      }
      testedJourneys += 1;
    }
  }
  expect(testedJourneys).toBe(56);
});

test('reranks preference requests without reloading routes and respects single-route journeys', async ({ page }) => {
  const journey = await selectJourney(page, 'grand_central_terminal', 'bryant_park');
  const expectedWinners: Record<string, string> = {
    'historic architecture': 'architecture',
    'shortest route': 'direct',
    'fastest walk': 'direct',
    'see as many interesting landmarks as possible': 'architecture',
    'I love architecture but need to arrive within 10 minutes': 'via_vanderbilt',
    'fast walk without architecture or landmarks': 'direct',
  };
  expect(journey.routes.route_count).toBe(3);

  for (const [preference, expectedRouteId] of Object.entries(expectedWinners)) {
    let routesRequested = 0;
    const listener = (request: { url: () => string }) => {
      if (request.url().includes('/api/routes')) routesRequested += 1;
    };
    page.on('request', listener);
    await page.getByLabel('What kind of walk do you want?').fill(preference);
    const responsePromise = page.waitForResponse((response) => {
      if (!response.url().includes('/api/recommend') || response.request().method() !== 'POST') return false;
      const body = response.request().postDataJSON() as { preference?: string };
      return body.preference === preference;
    });
    await page.getByRole('button', { name: 'Recommend' }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const recommendation = await response.json();
    expect(recommendation.recommended_route.route_id).toBe(expectedRouteId);
    expect(recommendation.distance_m).toBe(recommendation.recommended_route.distance_m);
    expect(recommendation.estimated_walking_time_s).toBe(recommendation.recommended_route.estimated_duration_s);
    expect(recommendation.explanation).toContain(recommendation.recommended_route.route_name);
    expect(recommendation.limitation_disclosure.toLowerCase()).toContain('unverified');
    await expect(page.getByText(recommendation.recommended_route.route_name, { exact: true }).first()).toBeVisible();
    await page.removeListener('request', listener);
    expect(routesRequested).toBe(0);
  }

  const parsedExclusions = await page.request.post(`${API_ORIGIN}/api/recommend`, {
    data: {
      origin_id: 'grand_central_terminal',
      destination_id: 'bryant_park',
      preference: 'avoid architecture and landmarks',
    },
  });
  expect(parsedExclusions.status()).toBe(200);
  const negativeRecommendation = await parsedExclusions.json();
  expect(negativeRecommendation.parsed_preferences.architecture).toBe(0);
  expect(negativeRecommendation.parsed_preferences.more_landmarks).toBe(0);
  expect(negativeRecommendation.recommended_route.route_id).toBe('direct');

  const unsupportedResponsePromise = page.waitForResponse((response) => {
    if (!response.url().includes('/api/recommend') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { preference?: string };
    return body.preference === 'I prefer greenery and quiet places.';
  });
  await page.getByLabel('What kind of walk do you want?').fill('I prefer greenery and quiet places.');
  await page.getByRole('button', { name: 'Recommend' }).click();
  const unsupportedResponse = await unsupportedResponsePromise;
  expect((await unsupportedResponse.json()).parsed_preferences.unsupported_preferences).toEqual(['greenery', 'quietness']);
  await expect(page.getByText(/Not scored because route data is unavailable: greenery, quietness/)).toBeVisible();

  await selectJourney(page, 'bryant_park', 'chrysler_building');
  const singleRoutePreference = page.waitForResponse((response) => {
    if (!response.url().includes('/api/recommend') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { preference?: string };
    return body.preference === 'historic architecture';
  });
  await page.getByLabel('What kind of walk do you want?').fill('historic architecture');
  await page.getByRole('button', { name: 'Recommend' }).click();
  const singleResponse = await singleRoutePreference;
  const singleRecommendation = await singleResponse.json();
  expect(singleRecommendation.recommended_route.route_id).toBe('direct');
  expect(singleRecommendation.ranked_alternatives).toHaveLength(0);
  expect(singleRecommendation.explanation.toLowerCase()).toContain('direct pedestrian route');
  expect(singleRecommendation.explanation).toContain(singleRecommendation.recommended_route.route_name);
  await expect(page.getByRole('heading', { name: 'Available Genuine Pedestrian Routes (1)' })).toBeVisible();
});

test('refreshes available alternatives on the map when a recommendation adds a cached route', async ({ page }) => {
  const originId = 'bryant_park';
  const destinationId = 'chrysler_building';
  await selectJourney(page, originId, destinationId);

  const routesResponse = await page.request.get(`${API_ORIGIN}/api/routes`, {
    params: { origin_id: originId, destination_id: destinationId },
  });
  const routesData = await routesResponse.json();
  expect(routesData.route_count).toBe(1);
  const alternative = JSON.parse(JSON.stringify(routesData.routes[0]));
  alternative.route_id = 'mock_generated_route';
  alternative.route_name = 'Mock generated alternative';
  alternative.geojson.name = alternative.route_id;
  alternative.geojson.features[0].properties.route_id = alternative.route_id;
  alternative.geojson.features[0].properties.route_name = alternative.route_name;
  const routesWithAlternative = {
    ...routesData,
    route_count: 2,
    routes: [...routesData.routes, alternative],
  };

  const recommendationResponse = await page.request.post(`${API_ORIGIN}/api/recommend`, {
    data: {
      origin_id: originId,
      destination_id: destinationId,
      preference: 'historic architecture',
    },
  });
  const recommendation = await recommendationResponse.json();
  recommendation.route_generation = {
    strategy: 'landmark',
    status: 'generated',
    reason: 'A new validated alternative is available.',
    generated_route_ids: ['mock_generated_route'],
    reused_route_count: 1,
    available_route_count: 2,
  };

  await page.route('**/api/recommend', async (route) => {
    const body = route.request().postDataJSON() as { preference?: string };
    if (body.preference === 'historic architecture') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(recommendation),
      });
      return;
    }
    await route.continue();
  });
  await page.route('**/api/routes?*', async (route) => {
    const url = new URL(route.request().url());
    if (
      url.searchParams.get('origin_id') === originId &&
      url.searchParams.get('destination_id') === destinationId
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(routesWithAlternative),
      });
      return;
    }
    await route.continue();
  });

  await page.getByLabel('What kind of walk do you want?').fill('historic architecture');
  const recommendationPromise = page.waitForResponse((response) =>
    response.url().includes('/api/recommend') &&
    response.request().method() === 'POST'
  );
  await page.getByRole('button', { name: 'Recommend' }).click();
  expect((await recommendationPromise).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Available Genuine Pedestrian Routes (2)' })).toBeVisible();
  await expect(page.getByText('Mock generated alternative', { exact: true }).first()).toBeVisible();
  expect(await mapPaths(page).count()).toBeGreaterThanOrEqual(2);
});

test('swaps endpoints and refreshes map; loading and route API errors clear stale geometry', async ({ page }) => {
  const originalOrigin = await originSelect(page).inputValue();
  const originalDestination = await destinationSelect(page).inputValue();
  const originalMapGeometry = await mapPaths(page).evaluateAll((paths) => paths.map((path) => path.getAttribute('d')));
  const swapResponse = waitForJourney(page, originalDestination, originalOrigin);
  await page.getByRole('button', { name: 'Swap Origin and Destination' }).click();
  const swapped = await swapResponse;
  expect(await originSelect(page).inputValue()).toBe(originalDestination);
  expect(await destinationSelect(page).inputValue()).toBe(originalOrigin);
  expect(swapped.routes.routes.length).toBeGreaterThan(0);
  await expect(mapMarkers(page)).toHaveCount(2);
  const swappedMapGeometry = await mapPaths(page).evaluateAll((paths) => paths.map((path) => path.getAttribute('d')));
  expect(swappedMapGeometry).not.toEqual(originalMapGeometry);
  await mapMarkers(page).first().click();
  const swappedLandmarks = await (await page.request.get(`${API_ORIGIN}/api/landmarks`)).json();
  const newOriginName = swappedLandmarks.landmarks.find((item: { id: string }) => item.id === originalDestination).name;
  await expect(page.locator('.leaflet-popup-content')).toContainText(newOriginName);

  let shouldFail = true;

  await page.route('**/api/routes?*', async (route) => {
    const url = new URL(route.request().url());

    if (
      shouldFail &&
      url.searchParams.get('origin_id') === 'bryant_park' &&
      url.searchParams.get('destination_id') === 'chrysler_building'
    ) {
      shouldFail = false;

      await route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'QA simulated route provider failure',
        }),
      });

      return;
    }

    await route.continue();
  });

  const failedRoutesResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());

    return (
      response.url().includes('/api/routes') &&
      url.searchParams.get('origin_id') === 'bryant_park' &&
      url.searchParams.get('destination_id') === 'chrysler_building'
    );
  });

  // The earlier swap leaves Bryant Park as the origin.
  // Change only the destination to avoid selecting an already-selected endpoint.
  await destinationSelect(page).selectOption('chrysler_building');

  expect((await failedRoutesResponse).status()).toBe(502);
  await expect(page.getByText('Fetching genuine pedestrian routes...', { exact: true })).toBeHidden();
  await expect(page.getByText('Unable to Process Request')).toBeVisible();
  await expect(page.getByText('QA simulated route provider failure')).toBeVisible();
  await expect(routeMap(page)).toHaveCount(0);
  await expect(mapPaths(page)).toHaveCount(0);

  const retryResponse = waitForJourney(
    page,
    'bryant_park',
    'chrysler_building',
  );
  await page.getByRole('button', { name: 'Retry' }).click();
  const retryResult = await retryResponse;
  expect(retryResult.routes.route_count).toBeGreaterThan(0);
  await expect(routeMap(page)).toBeVisible();
});

test('shows route loading state and handles zero available alternatives without a blank dashboard', async ({ page }) => {
  await page.route('**/api/routes?*', async (route) => {
    const url = new URL(route.request().url());

    if (
      url.searchParams.get('origin_id') === 'bryant_park' &&
      url.searchParams.get('destination_id') === 'chrysler_building'
    ) {
      await new Promise((resolve) => setTimeout(resolve, 350));
    }

    await route.continue();
  });

  // First change the destination while keeping the current origin.
  const setupResponse = waitForJourney(
    page,
    'grand_central_terminal',
    'chrysler_building',
  );

  await destinationSelect(page).selectOption('chrysler_building');
  await setupResponse;

  // Now change the origin. This triggers the delayed target request.
  const responsePromise = waitForJourney(
    page,
    'bryant_park',
    'chrysler_building',
  );

  await originSelect(page).selectOption('bryant_park');

  await expect(
    page.getByText('Fetching genuine pedestrian routes...', { exact: true }),
  ).toBeVisible();

  const result = await responsePromise;

  expect(result.routes.route_count).toBe(1);
  expect(result.recommendation.ranked_alternatives).toHaveLength(0);
  expect(result.recommendation.explanation.toLowerCase()).toContain(
    'direct pedestrian route',
  );

  await expect(
    page.getByRole('heading', {
      name: 'Available Genuine Pedestrian Routes (1)',
    }),
  ).toBeVisible();

  await expect(
    page.getByText('No recommendation available.'),
  ).toHaveCount(0);

  await expect(routeMap(page)).toBeVisible();
});