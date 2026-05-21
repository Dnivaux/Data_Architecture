import { useState, useEffect } from 'react';
import { api } from '../api/client';

/**
 * Charge le détail de couverture réseau par opérateur
 * pour un arrondissement donné.
 */
export function useOperators(arrondissement) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState(null);

  useEffect(() => {
    if (!arrondissement) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.connectivity
      .operators(arrondissement)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [arrondissement]);

  return { data, loading, error };
}
