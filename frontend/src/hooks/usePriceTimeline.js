import { useState, useEffect, useMemo } from 'react';
import { api } from '../api/client';

/**
 * Charge l'intégralité de la série temporelle des prix DVF (tous arrondissements,
 * toutes années) en un seul appel, puis l'indexe par année → {arr: prix}.
 *
 * Alimente le slider temporel qui « rejoue » la choroplèthe des prix année par
 * année (attendu consigne : « timeline permettant de rejouer l'évolution
 * historique des tendances »).
 */
export function usePriceTimeline() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.prices
      .timeline()
      .then((data) => { if (!cancelled) setRows(Array.isArray(data) ? data : []); })
      .catch(() => { if (!cancelled) setRows([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const years = useMemo(
    () => Array.from(new Set(rows.map((r) => r.year))).sort((a, b) => a - b),
    [rows],
  );

  // Map année → { [arrondissement]: median_price }
  const priceByYear = useMemo(() => {
    const m = new Map();
    rows.forEach((r) => {
      if (!m.has(r.year)) m.set(r.year, {});
      m.get(r.year)[r.arrondissement] = r.median_price ?? null;
    });
    return m;
  }, [rows]);

  return { years, priceByYear, loading, hasData: years.length > 0 };
}
