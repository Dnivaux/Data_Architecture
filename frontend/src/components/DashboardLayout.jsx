import Sidebar from './Sidebar';
import KPIGrid from './KPIGrid';
import InteractiveMap from './InteractiveMap';
import AnalyticsPanel from './AnalyticsPanel';
import LiveStatusBadge from './LiveStatusBadge';

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
  const selectedScore = selectedArrondissement
    ? scoreMap?.[selectedArrondissement] ?? null
    : computeGlobalAverage(scores);

  const selectedIndicatorData = selectedArrondissement
    ? indicatorMap?.[selectedArrondissement] ?? null
    : null;

  const globalStats = computeGlobalStats(scores);

  return (
    <div className="flex h-screen overflow-hidden">

      {/* ── Sidebar ── */}
      <Sidebar
        selectedIndicator={selectedIndicator}
        onIndicatorChange={onIndicatorChange}
        liveMetrics={liveMetrics}
        globalStats={globalStats}
      />

      {/* ── Zone principale ── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* Header */}
        <header className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-slate-700 bg-slate-800/40">
          <div>
            <h1 className="text-base font-bold text-slate-100">
              {selectedArrondissement
                ? `Paris ${selectedArrondissement}e arrondissement`
                : 'Paris — Vue globale'}
            </h1>
            <p className="text-xs text-slate-500">
              Données Medallion · Gold Layer · PostgreSQL
            </p>
          </div>
          <div className="flex items-center gap-4">
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

/** Calcule la moyenne Paris pour les KPI Cards quand aucun arrdt sélectionné */
function computeGlobalAverage(scores) {
  if (!scores?.length) return null;
  const keys = [
    'anime_score', 'calme_score', 'accessibilite_score',
    'connectivity_score', 'mobility_score', 'health_env_score',
    'tranquility_score', 'livability_score', 'median_price',
    'social_housing_pct', 'bar_count', 'nightclub_count', 'park_count',
  ];
  const avg = {};
  keys.forEach((k) => {
    const vals = scores.map((s) => s[k]).filter((v) => v != null);
    avg[k] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  return avg;
}

function computeGlobalStats(scores) {
  if (!scores?.length) return null;
  const withLiv = scores.filter((s) => s.livability_score != null);
  const withPrice = scores.filter((s) => s.median_price != null);
  if (!withLiv.length) return null;

  const best = withLiv.reduce((a, b) => (a.livability_score > b.livability_score ? a : b));
  const cheapest = withPrice.length
    ? withPrice.reduce((a, b) => (a.median_price < b.median_price ? a : b))
    : null;
  const avgLivability = (withLiv.reduce((s, d) => s + d.livability_score, 0) / withLiv.length).toFixed(1);
  const avgPrice = withPrice.length
    ? Math.round(withPrice.reduce((s, d) => s + d.median_price, 0) / withPrice.length)
    : null;

  return {
    avgLivability,
    avgPrice,
    bestArr: best.arrondissement,
    cheapestArr: cheapest?.arrondissement,
  };
}
