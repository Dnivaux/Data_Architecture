/**
 * Utilitaires de couleur pour les scores (0-100) et les prix.
 * Utilisés par la carte choroplèthe et les KPI Cards.
 */

/** Score 0-100 → couleur hex sur l'échelle rouge-ambre-vert */
export function scoreToHex(score) {
  if (score == null) return '#D0D7DE'; // neutre : donnée manquante
  const s = Math.max(0, Math.min(100, score));
  if (s >= 70) return '#22C55E'; // green
  if (s >= 50) return '#F59E0B'; // amber
  return '#F43F5E';              // coral
}

/** Score 0-100 → classe Tailwind text- */
export function scoreToTextClass(score) {
  if (score == null) return 'text-[#64748B]';
  if (score >= 70) return 'text-[#22C55E]';
  if (score >= 50) return 'text-[#F59E0B]';
  return 'text-[#F43F5E]';
}

/** Score 0-100 → classe Tailwind bg- (barre de progression) */
export function scoreToBgClass(score) {
  if (score == null) return 'bg-[#D0D7DE]';
  if (score >= 70) return 'bg-[#22C55E]';
  if (score >= 50) return 'bg-[#F59E0B]';
  return 'bg-[#F43F5E]';
}

/**
 * Interpolation continue rouge→vert pour la choroplèthe.
 * Retourne une couleur rgba exploitable par Leaflet.
 */
export function scoreToChoroplethColor(score, alpha = 0.75) {
  if (score == null) return `rgba(208, 215, 222, ${alpha})`;
  const s = Math.max(0, Math.min(100, score));
  const t = s / 100;
  // Rouge pur (s=0) → Vert pur (s=100)
  const r = Math.round(244 * (1 - t) + 34 * t);
  const g = Math.round(63  * (1 - t) + 197 * t);
  const b = Math.round(94  * (1 - t) + 94 * t);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Prix (€/m²) → couleur choroplèthe (plus cher = plus rouge).
 * Nécessite min/max du dataset pour normaliser.
 */
export function priceToChoroplethColor(price, min, max, alpha = 0.75) {
  if (price == null || min === max) return `rgba(208, 215, 222, ${alpha})`;
  // Prix élevé = rouge, prix bas = vert
  const t = Math.max(0, Math.min(1, (price - min) / (max - min)));
  return scoreToChoroplethColor(100 * (1 - t), alpha);
}

/** Couleur par indicateur + valeur (dispatch automatique) */
export function indicatorColor(indicatorId, value, allValues = [], alpha = 0.75) {
  if (indicatorId === 'median_price') {
    const nums = allValues.filter(Boolean);
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    return priceToChoroplethColor(value, min, max, alpha);
  }
  return scoreToChoroplethColor(value, alpha);
}
