/**
 * Utilitaires de couleur pour les scores (0-100) et les prix.
 * Utilisés par la carte choroplèthe et les KPI Cards.
 */

/** Score 0-100 → couleur hex sur l'échelle vert-jaune-orange-rouge premium */
export function scoreToHex(score) {
  if (score == null) return '#D0D7DE'; // neutre : donnée manquante
  const s = Math.max(0, Math.min(100, score));
  if (s >= 75) return '#10B981'; // Vert émeraude
  if (s >= 55) return '#84CC16'; // Lime
  if (s >= 40) return '#FACC15'; // Jaune
  if (s >= 25) return '#F97316'; // Orange
  return '#EF4444';              // Rouge
}

/** Score 0-100 → classe Tailwind text- (adapté pour le contraste sur fond blanc) */
export function scoreToTextClass(score) {
  if (score == null) return 'text-[#64748B]';
  if (score >= 75) return 'text-[#059669]'; // Vert-600
  if (score >= 55) return 'text-[#65A30D]'; // Lime-600
  if (score >= 40) return 'text-[#CA8A04]'; // Yellow-600 (assure un bon contraste sur fond blanc)
  if (score >= 25) return 'text-[#EA580C]'; // Orange-600
  return 'text-[#DC2626]';                  // Red-600
}

/** Score 0-100 → classe Tailwind bg- (barre de progression) */
export function scoreToBgClass(score) {
  if (score == null) return 'bg-[#D0D7DE]';
  if (score >= 75) return 'bg-[#10B981]';
  if (score >= 55) return 'bg-[#84CC16]';
  if (score >= 40) return 'bg-[#FACC15]';
  if (score >= 25) return 'bg-[#F97316]';
  return 'bg-[#EF4444]';
}

/**
 * Interpolation continue rouge→vert pour la choroplèthe.
 * Évite le marron grâce à des arrêts intermédiaires saturés (Orange, Jaune, Lime).
 * Retourne une couleur rgba exploitable par Leaflet.
 */
export function scoreToChoroplethColor(score, alpha = 0.8) {
  if (score == null) return `rgba(208, 215, 222, ${alpha})`;
  const s = Math.max(0, Math.min(100, score));
  const t = s / 100;
  
  const stops = [
    { t: 0,    color: { r: 239, g: 68, b: 68 } },   // Rouge (#EF4444)
    { t: 0.28, color: { r: 249, g: 115, b: 22 } },  // Orange (#F97316)
    { t: 0.48, color: { r: 250, g: 204, b: 21 } },  // Jaune (#FACC15)
    { t: 0.65, color: { r: 132, g: 204, b: 22 } },  // Lime (#84CC16)
    { t: 1,    color: { r: 16, g: 185, b: 129 } },  // Vert Émeraude (#10B981)
  ];
  
  const lerp = (a, b, f) => Math.round(a + (b - a) * f);
  
  const stop = stops.find((s, i) => t <= s.t || i === stops.length - 1);
  const fromIndex = stops.indexOf(stop) - 1;
  const from = stops[fromIndex < 0 ? 0 : fromIndex];
  
  const denominator = stop.t - from.t;
  const f = denominator === 0 ? 0 : (t - from.t) / denominator;
  
  const r = lerp(from.color.r, stop.color.r, f);
  const g = lerp(from.color.g, stop.color.g, f);
  const b = lerp(from.color.b, stop.color.b, f);
  
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Prix (€/m²) → couleur choroplèthe (plus cher = plus rouge).
 * Nécessite min/max du dataset pour normaliser.
 */
export function priceToChoroplethColor(price, min, max, alpha = 0.8) {
  if (price == null || min === max) return `rgba(208, 215, 222, ${alpha})`;
  // Prix élevé = rouge, prix bas = vert
  const t = Math.max(0, Math.min(1, (price - min) / (max - min)));
  return scoreToChoroplethColor(100 * (1 - t), alpha);
}

/**
 * Indicateurs dont la valeur brute doit être normalisée min-max
 * (pas des scores 0-100, mais des quantités absolues).
 * Plus = meilleur (vert) sauf median_price (plus = rouge).
 */
const _MINMAX_HIGHER_BETTER = new Set(['nombre_logements_sociaux', 'median_income']);
// Plus la valeur est basse, mieux c'est (air pur, peu de pollen, prix bas)
const _MINMAX_LOWER_BETTER  = new Set(['median_price', 'european_aqi', 'pollen_total']);

/** Couleur par indicateur + valeur (dispatch automatique) */
export function indicatorColor(indicatorId, value, allValues = [], alpha = 0.8) {
  if (_MINMAX_LOWER_BETTER.has(indicatorId)) {
    const nums = allValues.filter((v) => v != null);
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    return priceToChoroplethColor(value, min, max, alpha);
  }
  if (_MINMAX_HIGHER_BETTER.has(indicatorId)) {
    const nums = allValues.filter((v) => v != null);
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    if (min === max || value == null) return `rgba(154, 166, 178, ${alpha})`;
    const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
    return scoreToChoroplethColor(t * 100, alpha);
  }
  return scoreToChoroplethColor(value, alpha);
}

