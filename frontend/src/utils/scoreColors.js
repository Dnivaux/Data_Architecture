/**
 * Utilitaires de couleur pour les scores (0-100) et les prix.
 * Utilisés par la carte choroplèthe et les KPI Cards.
 */

/** Score 0-100 → couleur hex sur l'échelle bleu-vert premium */
export function scoreToHex(score) {
  if (score == null) return '#D0D7DE'; // neutre : donnée manquante
  const s = Math.max(0, Math.min(100, score));
  if (s >= 70) return '#00A3FF'; // bright azure
  if (s >= 50) return '#1974D2'; // medium blue
  return '#0F3B81';              // deep navy
}

/** Score 0-100 → classe Tailwind text- */
export function scoreToTextClass(score) {
  if (score == null) return 'text-[#64748B]';
  if (score >= 70) return 'text-[#00A3FF]';
  if (score >= 50) return 'text-[#1974D2]';
  return 'text-[#0F3B81]';
}

/** Score 0-100 → classe Tailwind bg- (barre de progression) */
export function scoreToBgClass(score) {
  if (score == null) return 'bg-[#D0D7DE]';
  if (score >= 70) return 'bg-[#00A3FF]';
  if (score >= 50) return 'bg-[#1974D2]';
  return 'bg-[#0F3B81]';
}

/**
 * Interpolation continue rouge→vert pour la choroplèthe.
 * Retourne une couleur rgba exploitable par Leaflet.
 */
export function scoreToChoroplethColor(score, alpha = 0.8) {
  if (score == null) return `rgba(208, 215, 222, ${alpha})`;
  const s = Math.max(0, Math.min(100, score));
  const t = s / 100;
  // Pure blue scale with more granularity
  const stops = [
    { t: 0,    color: { r: 15, g: 59, b: 129 } },   // deep navy
    { t: 0.35, color: { r: 20, g: 80, b: 160 } },   // mid-navy
    { t: 0.6,  color: { r: 25, g: 116, b: 210 } },  // medium blue
    { t: 0.8,  color: { r: 10, g: 140, b: 235 } },  // azure
    { t: 1,    color: { r: 0, g: 163, b: 255 } },    // bright azure
  ];
  const lerp = (a, b, f) => Math.round(a + (b - a) * f);
  
  const stop = stops.find((s, i) => t <= s.t || i === stops.length - 1);
  const fromIndex = stops.indexOf(stop) - 1;
  const from = stops[fromIndex < 0 ? 0 : fromIndex];
  
  const f = (t - from.t) / (stop.t - from.t);
  
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
const _MINMAX_HIGHER_BETTER = new Set(['nombre_logements_sociaux']);
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
