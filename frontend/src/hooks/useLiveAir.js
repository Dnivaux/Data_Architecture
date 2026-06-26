import { useState, useEffect, useRef, useCallback } from 'react';

const LIVE_AIR_ENDPOINT = '/api/live/air/latest';
const DEFAULT_INTERVAL_MS = 30_000; // 30 secondes

/**
 * Hook de polling temps réel de la qualité de l'air.
 *
 * Lit /api/live/air/latest (alimenté par le daemon micro-batch
 * src/ingestion/air_quality_micro_batch.py) toutes les 30 s.
 *
 * Returns :
 *   aqiByArr   — { [arrondissement]: european_aqi } pour la choroplèthe live
 *   byArr      — tableau brut des métriques par arrondissement (AQI, PM2.5, NO2…)
 *   totals     — agrégats Paris (moyenne AQI, pire arrondissement…)
 *   batchTs    — horodatage du dernier lot collecté
 *   isLive     — true dès le premier poll réussi avec données
 *   lastUpdate — timestamp (ms) du dernier poll réussi
 */
export function useLiveAir(intervalMs = DEFAULT_INTERVAL_MS) {
  const [aqiByArr, setAqiByArr] = useState(null);
  const [byArr, setByArr]       = useState([]);
  const [totals, setTotals]     = useState(null);
  const [batchTs, setBatchTs]   = useState(null);
  const [isLive, setIsLive]     = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);

  const timerRef   = useRef(null);
  const abortRef   = useRef(null);
  const mountedRef = useRef(true);

  const poll = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    if (!mountedRef.current) return;

    try {
      const res = await fetch(LIVE_AIR_ENDPOINT, { signal: abortRef.current.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (!mountedRef.current) return;
      if (json.status !== 'ok' || !Array.isArray(json.by_arrondissement)) return;

      const map = {};
      json.by_arrondissement.forEach((r) => {
        if (r.arrondissement != null && r.european_aqi != null) {
          map[r.arrondissement] = r.european_aqi;
        }
      });
      setAqiByArr(map);
      setByArr(json.by_arrondissement);
      setTotals(json.totals ?? null);
      setBatchTs(json.batch_ts ?? null);
      setIsLive(true);
      setLastUpdate(Date.now());
    } catch (err) {
      if (err.name === 'AbortError') return;
      // erreur transitoire : on conserve isLive (badge reste vert)
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    poll();
    timerRef.current = setInterval(poll, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(timerRef.current);
      abortRef.current?.abort();
    };
  }, [poll, intervalMs]);

  return { aqiByArr, byArr, totals, batchTs, isLive, lastUpdate, pollNow: poll };
}
