import { useState, useEffect } from 'react';
import { api } from '../api/client';
import { withAffordability } from '../utils/formatters';

/**
 * Charge les scores à la maille IRIS (~992 zones) avec géométrie WKT :
 *   - /api/iris/indicators/all → IrisDetail[] (choroplèthe infra-arrondissement)
 *
 * L'IRIS est le grain primaire : il rend visible le détail *à l'intérieur*
 * d'un arrondissement (prix, revenus, dynamisme variant rue par rue).
 */
export function useIris() {
  const [iris, setIris] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.iris
      .indicators()
      .then((data) => {
        if (!cancelled) {
          setIris(Array.isArray(data) ? withAffordability(data) : []);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message ?? 'IRIS indisponibles');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  return { iris, loading, error };
}
