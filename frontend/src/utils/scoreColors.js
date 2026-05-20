/**
 * Utilitaires de couleur pour les scores (0-100) et les prix.
 * Utilisés par la carte choroplèthe et les KPI Cards.
 */

/** Score 0-100 → couleur hex sur l'échelle rouge-ambre-vert */
export function scoreToHex(score) {
  if (score == null) return '#475569'; // slate-600 : donnée manquante
  const s = Math.max(0, Math.min(100, score));
  if (s >= 70) return '#10B981'; // emerald-500
  if (s >= 50) return '#F59E0B'; // amber-500
  if (s >= 30) return '#F97316'; // orange-500
  return '#EF4444';              // red-500
}

/** Score 0-100 → classe Tailwind text- */
export function scoreToTextClass(score) {
  if (score == null) return 'text-slate-500';
  if (score >= 70) return 'text-emerald-400';
  if (score >= 50) return 'text-amber-400';
  if (score >= 30) return 'text-orange-400';
  return 'text-red-400';
}

/** Score 0-100 → classe Tailwind bg- (barre de progression) */
export function scoreToBgClass(score) {
  if (score == null) return 'bg-slate-600';
  if (score >= 70) return 'bg-emerald-500';
  if (score >= 50) return 'bg-amber-500';
  if (score >= 30) return 'bg-orange-500';
  return 'bg-red-500';
}

/**
 * Interpolation continue rouge→vert pour la choroplèthe.
 * Retourne une couleur rgba exploitable par Leaflet.
 */
export function scoreToChoroplethColor(score, alpha = 0.75) {
  if (score == null) return `rgba(71, 85, 105, ${alpha})`;
  const s = Math.max(0, Math.min(100, score));
  const t = s / 100;
  // Rouge pur (s=0) → Vert pur (s=100)
  const r = Math.round(239 * (1 - t) + 16 * t);
  const g = Math.round(68  * (1 - t) + 185 * t);
  const b = Math.round(68  * (1 - t) + 129 * t);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Prix (€/m²) → couleur choroplèthe (plus cher = plus rouge).
 * Nécessite min/max du dataset pour normaliser.
 */
export function priceToChoroplethColor(price, min, max, alpha = 0.75) {
  if (price == null || min === max) return `rgba(71, 85, 105, ${alpha})`;
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
