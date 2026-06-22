import Sidebar, { INDICATOR_OPTIONS } from './Sidebar';
import KPIGrid from './KPIGrid';
import InteractiveMap from './InteractiveMap';
import AnalyticsPanel from './AnalyticsPanel';
import LiveStatusBadge from './LiveStatusBadge';
import { useChantiers } from '../hooks/useChantiers';
import { useState } from 'react';

/**
 * Layout principal de l'application :
 *
 *  ┌──────────────────────────────────────────────────────────┐
 *  │ HEADER  Urban Data Explorer            [Live Badge]      │
 *  ├──────────┬───────────────────────────────────────────────┤
 *  │          │  KPI GRID (6 cartes)                          │
 *  │ SIDEBAR  ├────────────────────────────┬──────────────────┤
 *  │          │   CARTE INTERACTIVE (60%)  │  ANALYTICS (40%) │
 *  └──────────┴────────────────────────────┴──────────────────┘
 */
export default function DashboardLayout({
  selectedArrondissement,
  onSelectArrondissement,
  selectedIndicator,
  onIndicatorChange,
  scores,
  indicators,
  scoreMap,
  indicatorMap,
  liveMetrics,
}) {
  const [showChantiers, setShowChantiers] = useState(false);
  const { chantiers } = useChantiers(selectedArrondissement, showChantiers);

  const selectedScore = selectedArrondissement
    ? scoreMap?.[selectedArrondissement] ?? null
    : computeGlobalAverage(scores);

  const selectedIndicatorData = selectedArrondissement
    ? indicatorMap?.[selectedArrondissement] ?? null
    : null;

  const globalStats = computeGlobalStats(scores);

  return (
    <div className="flex h-screen overflow-hidden">

      {/* Sidebar hidden — controls migrated to header (to be removed later) */}
      <aside className="hidden" />

      {/* ── Zone principale ── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* Header */}
        <header className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-[#D0D7DE] bg-[#F4F6F9]">
          <div>
            <h1 className="text-base font-bold text-[#0F3B81]">
              {selectedArrondissement
                ? `Paris ${selectedArrondissement}e arrondissement`
                : 'Paris — Vue globale'}
            </h1>
            <p className="text-xs text-[#64748B]">
              Analyse de la qualité de vie
            </p>
          </div>

          {/* Controls migrated from the sidebar: indicator selector + chantiers toggle */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <label className="text-xs text-[#64748B] mr-2">Indicateur</label>
              <select
                value={selectedIndicator}
                onChange={(e) => onIndicatorChange(e.target.value)}
                className="text-sm px-2 py-1 border rounded-md bg-white text-[#0F3B81]"
              >
                {INDICATOR_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>{opt.label}</option>
                ))}
              </select>
            </div>

            <button
              onClick={() => setShowChantiers((v) => !v)}
              className={`px-3 py-1 rounded-md text-sm border transition-colors ${showChantiers ? 'bg-[#00A3FF]/15 text-[#0F3B81] border-[#00A3FF]/40' : 'text-[#64748B] hover:text-[#0F3B81]'}`}
            >
              <span className="material-icon align-middle mr-1">construction</span>
              Chantiers
            </button>

            {selectedArrondissement && (
              <button
                className="btn-ghost text-xs"
                onClick={() => onSelectArrondissement(null)}
              >
                ← Retour vue globale
              </button>
            )}

            <LiveStatusBadge
              isLive={liveMetrics?.isLive}
              lastUpdate={liveMetrics?.lastUpdate}
              label="Micro-batch actif"
            />
          </div>
        </header>

        {/* Contenu défilable */}
        <main className="flex-1 overflow-hidden flex flex-col gap-4 p-4">

          {/* KPI Grid */}
          <KPIGrid
            data={selectedScore}
            liveData={liveMetrics}
            onIndicatorClick={onIndicatorChange}
          />

          {/* Carte + Analytics */}
          <div className="flex-1 flex gap-4 min-h-0">

            {/* Carte choroplèthe (60%) */}
            <div className="flex-[6] min-w-0">
              <InteractiveMap
                indicators={indicators}
                selectedIndicator={selectedIndicator}
                selectedArrondissement={selectedArrondissement}
                onSelectArrondissement={onSelectArrondissement}
                chantiers={chantiers}
                showChantiers={showChantiers}
              />
            </div>

            {/* Analytics Panel (40%) */}
            <div className="flex-[4] min-w-0 min-h-0 overflow-y-auto">
              <AnalyticsPanel
                selectedArrondissement={selectedArrondissement}
                indicatorData={selectedIndicatorData}
                scoreData={selectedScore}
              />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/** Calcule la moyenne Paris pour les KPI Cards quand aucun arrdt sélectionné.
 *  Pour les comptages (nombre_logements_sociaux), on fait la somme totale Paris. */
function computeGlobalAverage(scores) {
  if (!scores?.length) return null;
  const avgKeys = [
    'anime_score', 'calme_score',
    'connectivity_score', 'mobility_score', 'health_env_score',
    'tranquility_score', 'livability_score', 'median_price',
    'social_housing_pct', 'bar_count', 'nightclub_count', 'park_count',
  ];
  const sumKeys = ['nombre_logements_sociaux'];  // totaux Paris, pas une moyenne
  const result = {};
  avgKeys.forEach((k) => {
    const vals = scores.map((s) => s[k]).filter((v) => v != null);
    result[k] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  sumKeys.forEach((k) => {
    const vals = scores.map((s) => s[k]).filter((v) => v != null);
    result[k] = vals.length ? vals.reduce((a, b) => a + b, 0) : null;
  });
  return result;
}

function computeGlobalStats(scores) {
  if (!scores?.length) return null;
  const withConnectivity = scores.filter((s) => s.connectivity_score != null);
  const withPrice = scores.filter((s) => s.median_price != null);
  if (!withConnectivity.length) return null;

  const best = withConnectivity.reduce((a, b) => (a.connectivity_score > b.connectivity_score ? a : b));
  const cheapest = withPrice.length
    ? withPrice.reduce((a, b) => (a.median_price < b.median_price ? a : b))
    : null;
  const avgConnectivity = (withConnectivity.reduce((s, d) => s + d.connectivity_score, 0) / withConnectivity.length).toFixed(1);
  const avgPrice = withPrice.length
    ? Math.round(withPrice.reduce((s, d) => s + d.median_price, 0) / withPrice.length)
    : null;

  return {
    avgConnectivity,
    avgPrice,
    bestArr: best.arrondissement,
    cheapestArr: cheapest?.arrondissement,
  };
}
