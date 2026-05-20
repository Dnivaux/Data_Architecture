import { useState, useEffect } from 'react';
import { api } from '../api/client';

/**
 * Charge en parallèle :
 *   - /api/scores/all        → scores bruts (20 arrondissements)
 *   - /api/scores/indicators/all → scores + geometry_wkt (choroplèthe)
 */
export function useScores() {
  const [scores, setScores] = useState([]);
  const [indicators, setIndicators] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    Promise.allSettled([api.scores.all(), api.scores.indicators()])
      .then(([resScores, resIndicators]) => {
        if (resScores.status === 'fulfilled')      setScores(resScores.value);
        if (resIndicators.status === 'fulfilled')  setIndicators(resIndicators.value);

        // N'affiche l'erreur que si LES DEUX ont échoué (dashboard inutilisable)
        if (resScores.status === 'rejected' && resIndicators.status === 'rejected') {
          setError(resScores.reason?.message ?? 'API inaccessible');
        } else if (resScores.status === 'rejected') {
          setError(`Scores partiellement indisponibles : ${resScores.reason?.message}`);
        } else {
          setError(null);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  /** Map arrondissement → ArrondissementScore pour accès O(1) */
  const scoreMap = Object.fromEntries(scores.map((s) => [s.arrondissement, s]));
  const indicatorMap = Object.fromEntries(indicators.map((s) => [s.arrondissement, s]));

  return { scores, indicators, scoreMap, indicatorMap, loading, error };
}
