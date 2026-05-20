import { useState, useEffect } from 'react';
import { api } from '../api/client';

/**
 * Charge l'historique de prix DVF pour un arrondissement donné.
 * Re-fetche automatiquement quand `arrondissement` change.
 */
export function usePrices(arrondissement) {
  const [prices, setPrices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!arrondissement) {
      setPrices([]);
      return;
    }
    setLoading(true);
    api.prices
      .arrondissement(arrondissement)
      .then((data) => {
        setPrices(data);
        setError(null);
      })
      .catch((err) => {
        setError(err.message);
        setPrices([]);
      })
      .finally(() => setLoading(false));
  }, [arrondissement]);

  return { prices, loading, error };
}
