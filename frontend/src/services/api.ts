import {
  LandmarksResponse,
  RoutesResponse,
  RecommendationResponse,
  V1RecommendationResponse,
} from '../types';
const API_BASE = '/api';

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      // Fallback to default message
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchLandmarks(): Promise<LandmarksResponse> {
  const res = await fetch(`${API_BASE}/landmarks`);
  return handleResponse<LandmarksResponse>(res);
}

export async function fetchRoutes(originId: string, destinationId: string): Promise<RoutesResponse> {
  const params = new URLSearchParams({
    origin_id: originId,
    destination_id: destinationId,
  });
  const res = await fetch(`${API_BASE}/routes?${params.toString()}`);
  return handleResponse<RoutesResponse>(res);
}

export async function getRecommendation(
  originId: string,
  destinationId: string,
  preference: string,
  viaLandmarkIds: string[] = []
): Promise<RecommendationResponse> {
  const res = await fetch(`${API_BASE}/recommend`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      origin_id: originId,
      destination_id: destinationId,
      preference: preference,
      via_landmark_ids: viaLandmarkIds,
    }),
  });
  return handleResponse<RecommendationResponse>(res);
}

export async function checkHealth(): Promise<{ status: string }> {
  const res = await fetch('/health');
  return handleResponse<{ status: string }>(res);
}
export async function getV1Recommendation(
  originId: string,
  destinationId: string,
  preference: string
): Promise<V1RecommendationResponse> {
  const res = await fetch(`${API_BASE}/v1/recommend`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      origin_id: originId,
      destination_id: destinationId,
      preference,
    }),
  });

  return handleResponse<V1RecommendationResponse>(res);
}