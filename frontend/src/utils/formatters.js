/** Formateurs d'affichage — Urban Data Explorer */

const FR = new Intl.NumberFormat('fr-FR');
const FR_CURRENCY = new Intl.NumberFormat('fr-FR', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 0,
});
const FR_PCT = new Intl.NumberFormat('fr-FR', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** 8450 → "8 450 €/m²" */
export const fmtPrice = (v) =>
  v != null ? `${FR.format(Math.round(v))} €/m²` : '—';

/** 25000 → "25 000 €" */
export const fmtEur = (v) =>
  v != null ? `${FR.format(Math.round(v))} €` : '—';

/** 0.173 → "17,3 %" */
export const fmtPct = (v) => (v != null ? FR_PCT.format(v / 100) : '—');

/** 73.4 → "73,4 / 100" */
export const fmtScore = (v) => (v != null ? `${FR.format(+v.toFixed(1))} / 100` : '—');

/** 73.4 → "73" (entier, pour les badges compacts) */
export const fmtScoreShort = (v) => (v != null ? String(Math.round(v)) : '—');

/** 1250 → "1 250" */
export const fmtInt = (v) => (v != null ? FR.format(v) : '—');

/** "2023-06-15T12:00:00" → "15/06/2023 12:00" */
export const fmtDatetime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(d);
};

/** Durée depuis un timestamp : "il y a 2 min" */
export const fmtRelative = (ts) => {
  if (!ts) return '';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return 'à l\'instant';
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  return `il y a ${Math.floor(diff / 3600)} h`;
};

/** Nom d'un arrondissement (1 → "1er", 2 → "2e", ...) */
export const fmtArrondissement = (n) => {
  if (!n) return '—';
  return n === 1 ? '1er arrondissement' : `${n}e arrondissement`;
};

export const ARRONDISSEMENT_NAMES = {
  1: 'Louvre', 2: 'Bourse', 3: 'Temple', 4: 'Hôtel-de-Ville',
  5: 'Panthéon', 6: 'Luxembourg', 7: 'Palais-Bourbon', 8: 'Élysée',
  9: 'Opéra', 10: 'Entrepôt', 11: 'Popincourt', 12: 'Reuilly',
  13: 'Gobelins', 14: 'Observatoire', 15: 'Vaugirard', 16: 'Passy',
  17: 'Batignolles-Monceau', 18: 'Butte-Montmartre', 19: 'Buttes-Chaumont',
  20: 'Ménilmontant',
};
