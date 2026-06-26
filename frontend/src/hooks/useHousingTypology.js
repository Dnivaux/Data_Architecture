import { useState, useEffect } from 'react';
import { api } from '../api/client';

/**
 * Charge la répartition du parc immobilier (typologie + surfaces) pour un
 * arrondissement. `arrondissement` null/0 → agrégat Paris entier.
 */
export function useHousingTypology(arrondissement) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.housing
      .typology(arrondissement || 0)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [arrondissement]);

  return { data, loading };
}
