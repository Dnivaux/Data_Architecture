import { useState, useEffect } from 'react';
import RadarScoreChart from './RadarScoreChart';
import PriceLineChart from './PriceLineChart';
import { usePrices } from '../hooks/usePrices';
import { useOperators } from '../hooks/useOperators';
import { fmtArrondissement, fmtInt, fmtPrice, ARRONDISSEMENT_NAMES } from '../utils/formatters';
import { api } from '../api/client';

/**
 * Panneau d'analyse (droite du dashboard, 40%).
 * Affiche : Radar des scores + évolution des prix + métriques brutes.
 * En mode comparaison, superpose 2 arrondissements sur le radar.
 */
export default function AnalyticsPanel({ selectedArrondissement, indicatorData, scoreData }) {
  const { prices, loading: pricesLoading, error: pricesError } = usePrices(selectedArrondissement);
  const { data: operatorData, loading: operatorsLoading } = useOperators(selectedArrondissement);
  const [compareWith, setCompareWith] = useState('');
  const [comparisonScore, setComparisonScore] = useState(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    setCompareWith('');
    setComparisonScore(null);
  }, [selectedArrondissement]);

  function handleCompare(e) {
    const val = parseInt(e.target.value, 10);
    setCompareWith(e.target.value);
    if (!val || val === selectedArrondissement) { setComparisonScore(null); return; }
    setComparing(true);
    api.scores.one(val)
      .then(setComparisonScore)
      .catch(() => setComparisonScore(null))
      .finally(() => setComparing(false));
  }

  const name = selectedArrondissement
    ? `Paris ${selectedArrondissement}e — ${ARRONDISSEMENT_NAMES[selectedArrondissement] ?? ''}`
    : null;

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto pr-1">

      {/* Titre */}
      <div>
        <h2 className="text-sm font-semibold text-slate-100">
          {name ?? 'Vue globale — Paris'}
        </h2>
        <p className="text-xs text-slate-500 mt-0.5">
          {selectedArrondissement
            ? fmtArrondissement(selectedArrondissement)
            : '20 arrondissements · Sélectionnez un arrondissement sur la carte'}
        </p>
      </div>

      {/* Comparaison */}
      {selectedArrondissement && (
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Comparer avec</label>
          <select className="select-field" value={compareWith} onChange={handleCompare}>
            <option value="">— Choisir un arrondissement —</option>
            {Array.from({ length: 20 }, (_, i) => i + 1)
              .filter((n) => n !== selectedArrondissement)
              .map((n) => (
                <option key={n} value={n}>
                  Paris {n}e — {ARRONDISSEMENT_NAMES[n]}
                </option>
              ))}
          </select>
        </div>
      )}

      {/* Radar Chart */}
      <div className="card">
        <p className="text-xs text-slate-400 uppercase tracking-wide mb-1">
          Profil des scores
        </p>
        <RadarScoreChart
          primary={scoreData}
          secondary={comparisonScore}
          labelA={selectedArrondissement ? `Paris ${selectedArrondissement}e` : 'Paris (moy.)'}
          labelB={compareWith ? `Paris ${compareWith}e` : undefined}
        />
        {comparing && (
          <p className="text-xs text-slate-500 text-center mt-1 animate-pulse">
            Chargement de la comparaison…
          </p>
        )}
      </div>

      {/* Prix DVF */}
      <div className="card">
        {pricesError && !pricesLoading ? (
          <div className="h-40 flex flex-col items-center justify-center gap-2">
            <span className="text-slate-500 text-xs uppercase tracking-wide">Prix médian DVF</span>
            <span className="text-amber-500/80 text-xs text-center leading-relaxed">
              Données indisponibles<br />
              <span className="text-slate-600">(table Gold non peuplée)</span>
            </span>
          </div>
        ) : (
          <PriceLineChart prices={prices} loading={pricesLoading} />
        )}
      </div>

      {/* Meilleur opérateur réseau */}
      {selectedArrondissement && (
        <ConnectivityDetail data={operatorData} loading={operatorsLoading} />
      )}

      {/* Métriques brutes (si arrondissement sélectionné) */}
      {indicatorData && <MetricsDetail data={indicatorData} />}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Bloc connectivité / meilleur opérateur
// ─────────────────────────────────────────────────────────────────
const OP_ICONS = { orange: '🟠', sfr: '🔴', bouygues: '🔵', free: '🟣' };

function ConnectivityDetail({ data, loading }) {
  if (loading) {
    return (
      <div className="card">
        <p className="text-xs text-slate-400 uppercase tracking-wide mb-2">Réseau</p>
        <p className="text-xs text-slate-500 animate-pulse">Chargement…</p>
      </div>
    );
  }
  if (!data) return null;

  const { ftth_pct, best_4g, best_5g, operators = [] } = data;

  return (
    <div className="card">
      <p className="text-xs text-slate-400 uppercase tracking-wide mb-3">Réseau &amp; Connectivité</p>

      {/* Meilleurs opérateurs */}
      <div className="flex gap-3 mb-3">
        {best_4g && (
          <div className="flex-1 bg-slate-700/50 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-500 uppercase">+ d'antennes 4G</p>
            <p className="text-xs font-semibold text-cyan-300 mt-0.5">{best_4g}</p>
          </div>
        )}
        {best_5g && (
          <div className="flex-1 bg-slate-700/50 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-500 uppercase">+ d'antennes 5G</p>
            <p className="text-xs font-semibold text-violet-300 mt-0.5">{best_5g}</p>
          </div>
        )}
        {ftth_pct != null && (
          <div className="flex-1 bg-slate-700/50 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-500 uppercase">Fibre éligible</p>
            <p className="text-xs font-semibold text-emerald-300 mt-0.5">{ftth_pct} %</p>
          </div>
        )}
      </div>

      {/* Détail par opérateur (% de part d'antennes dans l'arrondissement) */}
      {operators.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] text-slate-600 mb-1">Part des antennes par opérateur (source ARCEP 2025-T4)</p>
          {operators.map((op) => (
            <div key={op.operateur} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-slate-300 w-32">
                <span>{OP_ICONS[op.operateur] ?? '📶'}</span>
                {op.label}
              </span>
              <div className="flex gap-2">
                {op.pct_pop_4g != null && (
                  <span className="text-cyan-400">4G&nbsp;{op.pct_pop_4g}%</span>
                )}
                {op.pct_pop_5g != null && (
                  <span className="text-violet-400">5G&nbsp;{op.pct_pop_5g}%</span>
                )}
                {op.pct_pop_4g == null && op.pct_pop_5g == null && (
                  <span className="text-slate-600">—</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {operators.length === 0 && !best_4g && ftth_pct == null && (
        <p className="text-xs text-slate-600">Données ARCEP non disponibles — relancer le pipeline</p>
      )}
    </div>
  );
}

function MetricsDetail({ data }) {
  const rows = [
    { label: 'Stations Vélib\'',     value: fmtInt(data.station_count_velib),    icon: '🚲' },
    { label: 'Vélos dispos (moy.)',  value: data.avg_bikes_available != null ? `${data.avg_bikes_available?.toFixed(1)}` : '—', icon: '🔄' },
    { label: 'Îlots de fraîcheur',  value: fmtInt(data.nb_ilots_fraicheur),     icon: '🌳' },
    { label: 'Arbres / km²',        value: fmtInt(data.arbres_per_km2),         icon: '🌲' },
    { label: 'Crimes (total)',       value: fmtInt(data.crime_count_total),      icon: '🔒' },
    { label: 'Taux / 1 000 hab.',   value: data.crime_rate_per_1000 != null ? `${data.crime_rate_per_1000?.toFixed(1)}` : '—', icon: '📉' },
    { label: '% fibre éligible',    value: data.pct_eligible_ftth != null ? `${Math.round(data.pct_eligible_ftth)} %` : '—', icon: '📡' },
    { label: '% couv. 4G',          value: data.pct_pop_4g_mean   != null ? `${Math.round(data.pct_pop_4g_mean)} %` : '—',   icon: '📶' },
  ].filter((r) => r.value !== '—' && r.value !== 'undefined' && r.value != null);

  if (rows.length === 0) return null;

  return (
    <div className="card">
      <p className="text-xs text-slate-400 uppercase tracking-wide mb-3">Métriques détaillées</p>
      <div className="grid grid-cols-2 gap-y-2 gap-x-4">
        {rows.map(({ label, value, icon }) => (
          <div key={label} className="flex items-center gap-1.5">
            <span className="text-base" role="img">{icon}</span>
            <div>
              <p className="text-xs text-slate-500 leading-tight">{label}</p>
              <p className="text-sm font-medium text-slate-200">{value}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
