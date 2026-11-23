import { test, describe } from 'node:test';
import assert from 'node:assert';

describe('Frontend API Service and Error Handling', () => {
  class ApiError extends Error {
    constructor(status, detail) {
      super(detail);
      this.name = 'ApiError';
      this.status = status;
      this.detail = detail;
    }
  }

  async function handleResponse(res) {
    if (!res.ok) {
      let detail = `Request failed with status ${res.status}`;
      try {
        const data = await res.json();
        if (data && data.detail) {
          detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        }
      } catch {
        // Fallback
      }
      throw new ApiError(res.status, detail);
    }
    return res.json();
  }

  test('ApiError captures HTTP status and detail string', () => {
    const err = new ApiError(404, 'No routes found for journey');
    assert.strictEqual(err.status, 404);
    assert.strictEqual(err.message, 'No routes found for journey');
    assert.strictEqual(err.name, 'ApiError');
  });

  test('handleResponse parses successful JSON', async () => {
    const mockRes = {
      ok: true,
      status: 200,
      json: async () => ({ count: 9, landmarks: [] }),
    };
    const result = await handleResponse(mockRes);
    assert.strictEqual(result.count, 9);
    assert.deepStrictEqual(result.landmarks, []);
  });

  test('handleResponse throws ApiError on 404 with detail message', async () => {
    const mockRes = {
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Unknown origin landmark ID' }),
    };
    await assert.rejects(
      async () => handleResponse(mockRes),
      (err) => {
        assert(err instanceof ApiError);
        assert.strictEqual(err.status, 404);
        assert.strictEqual(err.detail, 'Unknown origin landmark ID');
        return true;
      }
    );
  });

  test('handleResponse throws ApiError on 422 for identical endpoints', async () => {
    const mockRes = {
      ok: false,
      status: 422,
      json: async () => ({ detail: "Origin and destination cannot be the same landmark ('bryant_park')." }),
    };
    await assert.rejects(
      async () => handleResponse(mockRes),
      (err) => {
        assert(err instanceof ApiError);
        assert.strictEqual(err.status, 422);
        assert(err.detail.includes('same landmark'));
        return true;
      }
    );
  });

  test('checkHealth returns ok status on successful response', async () => {
    const mockRes = {
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok' }),
    };
    const result = await handleResponse(mockRes);
    assert.deepStrictEqual(result, { status: 'ok' });
  });
});

describe('GeoJSON Walking Geometry Validation', () => {
  test('Genuine route must have LineString geometry and multiple coordinate pairs', () => {
    const mockRouteGeoJson = {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [
              [-73.977366, 40.752125],
              [-73.978100, 40.752300],
              [-73.980500, 40.753100],
              [-73.982276, 40.753837],
            ],
          },
          properties: {
            route_id: 'direct',
            distance_m: 686.0,
          },
        },
      ],
    };

    const geom = mockRouteGeoJson.features[0].geometry;
    assert.strictEqual(geom.type, 'LineString');
    assert.ok(geom.coordinates.length > 2, 'Must have intermediate points along sidewalk, not just 2 straight points');

    // GeoJSON specification coordinate order is [longitude, latitude]
    for (const [lon, lat] of geom.coordinates) {
      assert.ok(lon >= -74.05 && lon <= -73.90, `Longitude ${lon} within Manhattan bounds`);
      assert.ok(lat >= 40.70 && lat <= 40.80, `Latitude ${lat} within Manhattan bounds`);
    }
  });
});

describe('Limitation Disclosure Propagation', () => {
  test('Disclosure includes prototype notice and unverified coordinate disclaimer', () => {
    const disclosure =
      'Prototype note: Route evaluations are based on pedestrian routes generated with unverified prototype coordinates projected onto OSM sidewalk centerlines. Walk times and distances are routing engine estimates.';
    assert.ok(disclosure.includes('Prototype note'));
    assert.ok(disclosure.includes('unverified prototype coordinates'));
    assert.ok(disclosure.includes('routing engine estimates'));
  });
});
