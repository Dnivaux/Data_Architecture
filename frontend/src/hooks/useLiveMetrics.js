import { useState, useEffect, useRef, useCallback } from 'react';

const LIVE_ENDPOINT = '/api/mobility/live';
const DEFAULT_INTERVAL_MS = 30_000; // 30 secondes

/**
 * Hook de polling — lit /api/mobility/live toutes les 30s.
 *
 * Démontre le critère C2.4 "micro-batch temps réel" :
 * à chaque poll réussi, `isLive` passe à true et `lastUpdate` est mis à jour,
 * ce qui déclenche l'effet pulse Tailwind sur les KPI Cards de mobilité.
 *
 * Returns :
 *   data        — réponse JSON du dernier poll réussi
 *   lastUpdate  — timestamp (ms) du dernier poll réussi
 *   isLive      — true dès le premier poll réussi (reste true après)
 *   isLoading   — true pendant le poll en cours
 *   error       — message d'erreur du dernier poll échoué
 *   pollNow     — fonction pour déclencher un poll immédiat
 */
export function useLiveMetrics(intervalMs = DEFAULT_INTERVAL_MS) {
  const [data, setData]           = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [isLive, setIsLive]       = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError]         = useState(null);

  const timerRef     = useRef(null);
  const abortRef     = useRef(null);
  const mountedRef   = useRef(true);

  const poll = useCallback(async () => {
    // Annule un éventuel fetch en cours
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    if (!mountedRef.current) return;
    setIsLoading(true);

    try {
      const res = await fetch(LIVE_ENDPOINT, { signal: abortRef.current.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status} — ${res.statusText}`);

      const json = await res.json();

      if (!mountedRef.current) return;
      setData(json);
      setLastUpdate(Date.now());
      setIsLive(true);
      setError(null);
    } catch (err) {
      if (err.name === 'AbortError') return; // ignoré : fetch annulé volontairement
      if (!mountedRef.current) return;
      setError(err.message);
      // On ne reset pas isLive sur erreur transitoire : le badge reste vert
    } finally {
      if (mountedRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    // Premier poll immédiat au montage
    poll();

    // Ensuite toutes les 30s
    timerRef.current = setInterval(poll, intervalMs);

    return () => {
      mountedRef.current = false;
      clearInterval(timerRef.current);
      abortRef.current?.abort();
    };
  }, [poll, intervalMs]);

  return { data, lastUpdate, isLive, isLoading, error, pollNow: poll };
}
