/**
 * Client API — Urban Data Explorer
 * Tous les appels vers FastAPI passent par ce module.
 * En développement, Vite proxifie /api et /health vers localhost:8000.
 */

const BASE = import.meta.env.VITE_API_URL ?? '';

async function get(path, params = {}) {
  const url = new URL(BASE + path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v != null) url.searchParams.set(k, String(v));
  });

  const res = await fetch(url.toString());
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`[${res.status}] ${path} — ${detail}`);
  }
  return res.json();
}

export const api = {
  scores: {
    /** GET /api/scores/all → ArrondissementScore[] */
    all: () => get('/api/scores/all'),
    /** GET /api/scores/indicators/all → ArrondissementDetail[] (avec geometry_wkt) */
    indicators: () => get('/api/scores/indicators/all'),
    /** GET /api/scores/{n} → ArrondissementScore */
    one: (n) => get(`/api/scores/${n}`),
  },

  prices: {
    /** GET /api/prices/arrondissement/{n} → PriceTimeline[] */
    arrondissement: (n) => get(`/api/prices/arrondissement/${n}`),
    /** GET /api/prices/timeline → PriceTimeline[] (filtres optionnels) */
    timeline: ({ arrondissement, year_min, year_max } = {}) =>
      get('/api/prices/timeline', { arrondissement, year_min, year_max }),
    /** GET /api/prices/summary → {arrondissement, min_price, max_price, avg_price, ...}[] */
    summary: () => get('/api/prices/summary'),
  },

  comparison: {
    /** GET /api/comparison/?a=X&b=Y → ArrondissementComparison */
    compare: (a, b) => get('/api/comparison/', { a, b }),
    /** GET /api/comparison/ranking?score_field=X → {rang, arrondissement, ...}[] */
    ranking: (field = 'livability_score') =>
      get('/api/comparison/ranking', { score_field: field }),
  },

  poi: {
    /** GET /api/poi/ → POI[] */
    all: (limit = 500) => get('/api/poi/', { limit }),
    /** GET /api/poi/by-category/{cat} → POI[] */
    byCategory: (cat) => get(`/api/poi/by-category/${cat}`),
    /** GET /api/poi/arrondissement/{n} → POI[] */
    byArrondissement: (n, category) =>
      get(`/api/poi/arrondissement/${n}`, { category }),
  },

  /** GET /health → HealthCheckExtended */
  health: () => get('/health'),
};
