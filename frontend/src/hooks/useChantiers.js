import { useState, useEffect } from 'react';
import { api } from '../api/client';

/**
 * Charge les chantiers depuis /api/chantiers/live.
 * Ne lance la requête que si enabled=true.
 */
export function useChantiers(arrondissement, enabled) {
  const [chantiers, setChantiers] = useState([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);

  useEffect(() => {
    if (!enabled) {
      setChantiers([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.chantiers
      .live(arrondissement || undefined)
      .then((data) => {
        if (!cancelled) setChantiers(data.chantiers ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [arrondissement, enabled]);

  return { chantiers, loading, error };
}
