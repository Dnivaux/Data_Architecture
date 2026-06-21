/**
 * Client API — Urban Data Explorer
 * Tous les appels vers FastAPI passent par ce module.
 * En développement, Vite proxifie /api et /health vers localhost:8000.
 */

const BASE = import.meta.env.VITE_API_URL ?? '';
// Clé d'API optionnelle : envoyée si VITE_API_KEY est défini (auth backend activée).
const API_KEY = import.meta.env.VITE_API_KEY ?? '';

async function get(path, params = {}) {
  const url = new URL(BASE + path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v != null) url.searchParams.set(k, String(v));
  });

  const headers = API_KEY ? { 'X-API-Key': API_KEY } : undefined;
  // no-store : empêche le navigateur de resservir une vieille réponse en cache
  // (évite l'affichage de scores périmés après un rebuild du pipeline).
  const res = await fetch(url.toString(), { headers, cache: 'no-store' });
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

  chantiers: {
    /** GET /api/chantiers/live → {total, count, chantiers[]} */
    live: (arrondissement) => get('/api/chantiers/live', { arrondissement }),
  },

  connectivity: {
    /** GET /api/connectivity/{n}/operators → {operators[], ftth_pct, best_4g, best_5g} */
    operators: (n) => get(`/api/connectivity/${n}/operators`),
  },

  socialHousing: {
    /** GET /api/social-housing/timeline → SocialHousingPoint[] (évolution du parc social) */
    timeline: (arrondissement) => get('/api/social-housing/timeline', { arrondissement }),
  },

  /** GET /health → HealthCheckExtended */
  health: () => get('/health'),
};
