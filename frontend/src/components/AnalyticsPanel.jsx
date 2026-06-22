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

  // Prix DVF de l'arrondissement comparé (pour superposer la 2ᵉ courbe).
  const compareId = compareWith ? parseInt(compareWith, 10) : null;
  const { prices: comparePrices } = usePrices(compareId);

  const labelA = selectedArrondissement ? `Paris ${selectedArrondissement}e` : 'Paris (moy.)';
  const labelB = compareWith ? `Paris ${compareWith}e` : undefined;

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
        <h2 className="text-sm font-semibold text-slate-800">
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
          <label className="text-xs text-slate-500 mb-1 block">Comparer avec</label>
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
        <div className="flex items-start justify-between mb-1">
          <p className="text-xs text-slate-500 uppercase tracking-wide">
            Profil des scores
          </p>
          {comparisonScore && (
            <div className="flex items-center gap-3 shrink-0">
              <GlobalScoreBadge label={labelA} value={scoreData?.livability_score} color="#2563EB" />
              <GlobalScoreBadge label={labelB} value={comparisonScore?.livability_score} color="#10B981" />
            </div>
          )}
        </div>
        <RadarScoreChart
          primary={scoreData}
          secondary={comparisonScore}
          labelA={labelA}
          labelB={labelB}
        />
        {comparing && (
          <p className="text-xs text-[#64748B] text-center mt-1 animate-pulse">
            Chargement de la comparaison…
          </p>
        )}
      </div>

      {/* Prix DVF */}
      <div className="card">
        {pricesError && !pricesLoading ? (
          <div className="h-40 flex flex-col items-center justify-center gap-2">
            <span className="text-slate-500 text-xs uppercase tracking-wide">Prix médian DVF</span>
            <span className="text-rose-500 text-xs text-center leading-relaxed font-medium">
              Données indisponibles<br />
              <span className="text-slate-400 font-normal">(table Gold non peuplée)</span>
            </span>
          </div>
        ) : (
          <PriceLineChart
            prices={prices}
            comparePrices={compareId ? comparePrices : null}
            labelA={labelA}
            labelB={labelB}
            loading={pricesLoading}
          />
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

/** Badge « score global » (livability) affiché en haut à droite du radar en comparaison. */
function GlobalScoreBadge({ label, value, color }) {
  return (
    <div className="text-right leading-tight">
      <p className="text-[10px] uppercase tracking-wide" style={{ color }}>{label}</p>
      <p className="text-sm font-semibold" style={{ color }}>
        {value != null ? `${value.toFixed(1)} / 100` : '—'}
      </p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Bloc connectivité / meilleur opérateur
// ─────────────────────────────────────────────────────────────────
const OP_ICONS = {
  orange: { icon: 'fiber_manual_record', color: '#FF6600' },
  sfr: { icon: 'fiber_manual_record', color: '#E2001A' },
  bouygues: { icon: 'fiber_manual_record', color: '#009FDF' },
  free: { icon: 'fiber_manual_record', color: '#CB1C1B' },
};

function ConnectivityDetail({ data, loading }) {
  if (loading) {
    return (
      <div className="card">
        <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Réseau</p>
        <p className="text-xs text-slate-400 animate-pulse">Chargement…</p>
      </div>
    );
  }
  if (!data) return null;

  const { ftth_pct, best_4g, best_5g, operators = [] } = data;

  return (
    <div className="card">
      <p className="text-xs text-slate-500 uppercase tracking-wide mb-3">Réseau &amp; Connectivité</p>

      {/* Meilleurs opérateurs */}
      <div className="flex gap-3 mb-3">
        {best_4g && (
          <div className="flex-1 bg-slate-50 border border-slate-150 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-400 uppercase">Meilleur 4G</p>
            <p className="text-xs font-semibold text-blue-600 mt-0.5">{best_4g}</p>
          </div>
        )}
        {best_5g && (
          <div className="flex-1 bg-slate-50 border border-slate-150 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-400 uppercase">Meilleur 5G</p>
            <p className="text-xs font-semibold text-emerald-600 mt-0.5">{best_5g}</p>
          </div>
        )}
        {ftth_pct != null && (
          <div className="flex-1 bg-slate-50 border border-slate-150 rounded-lg p-2 text-center">
            <p className="text-[10px] text-slate-400 uppercase">Fibre</p>
            <p className="text-xs font-semibold text-emerald-600 mt-0.5">{ftth_pct} %</p>
          </div>
        )}
      </div>

      {/* Détail par opérateur (% de part d'antennes dans l'arrondissement) */}
      {operators.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] text-slate-400 mb-1">Part des antennes par opérateur (source ARCEP 2025-T4)</p>
          {operators.map((op) => (
            <div key={op.operateur} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-slate-700 w-32">
                <span
                  className="material-icon"
                  style={{ color: OP_ICONS[op.operateur]?.color ?? '#94A3B8' }}
                >
                  {OP_ICONS[op.operateur]?.icon ?? 'signal_cellular_alt'}
                </span>
                {op.label}
              </span>
              <div className="flex gap-2">
                {op.pct_pop_4g != null && (
                  <span className="text-blue-600 font-medium">4G&nbsp;{op.pct_pop_4g}%</span>
                )}
                {op.pct_pop_5g != null && (
                  <span className="text-emerald-600 font-medium">5G&nbsp;{op.pct_pop_5g}%</span>
                )}
                {op.pct_pop_4g == null && op.pct_pop_5g == null && (
                  <span className="text-slate-400">—</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {operators.length === 0 && !best_4g && ftth_pct == null && (
        <p className="text-xs text-slate-400">Données ARCEP non disponibles — relancer le pipeline</p>
      )}
    </div>
  );
}

function MetricsDetail({ data }) {
  const allMetrics = {
    median_price: { label: "Prix m² médian DVF", value: fmtPrice(data.median_price), icon: 'home' },
    nombre_logements_sociaux: { label: "Logements sociaux", value: fmtInt(data.nombre_logements_sociaux), icon: 'apartment' },
    pct_eligible_ftth: { label: "Éligibles fibre", value: data.pct_eligible_ftth != null ? `${Math.round(data.pct_eligible_ftth)}%` : null, icon: 'signal_wifi_4_bar' },
    avg_rate_dl_5g_mbps: { label: "Couv. 5G (débit)", value: data.avg_rate_dl_5g_mbps != null ? `${Math.round(data.avg_rate_dl_5g_mbps)} Mbps` : null, icon: 'phone_iphone' },
    station_count_velib: { label: "Stations Vélib'", value: fmtInt(data.station_count_velib), icon: 'directions_bike' },
    avg_bikes_available: { label: "Vélos dispos (moy.)", value: data.avg_bikes_available != null ? `${data.avg_bikes_available?.toFixed(1)}` : null, icon: 'sync' },
    metro_count: { label: "Stations métro", value: fmtInt(data.metro_count), icon: 'subway' },
    rer_count: { label: "Gares RER", value: fmtInt(data.rer_count), icon: 'train' },
    tram_count: { label: "Arrêts tram", value: fmtInt(data.tram_count), icon: 'tram' },
    bus_count: { label: "Arrêts bus", value: fmtInt(data.bus_count), icon: 'directions_bus' },
    park_count: { label: "Parcs & jardins", value: fmtInt(data.park_count), icon: 'park' },
    nb_ilots_fraicheur: { label: "Îlots de fraîcheur", value: fmtInt(data.nb_ilots_fraicheur), icon: 'ac_unit' },
    arbres_per_km2: { label: "Arbres / km²", value: fmtInt(data.arbres_per_km2), icon: 'forest' },
    european_aqi: { label: "Qualité de l'air (AQI)", value: data.european_aqi != null ? `${Math.round(data.european_aqi)}` : null, icon: 'air' },
    pollen_risk: { label: "Risque pollen", value: data.pollen_risk, icon: 'grass' },
    restaurant_count: { label: "Restaurants", value: fmtInt(data.restaurant_count), icon: 'restaurant' },
    bar_count: { label: "Bars", value: fmtInt(data.bar_count), icon: 'local_bar' },
    shop_count: { label: "Commerces", value: fmtInt(data.shop_count), icon: 'storefront' },
    crime_count_total: { label: "Crimes & délits", value: fmtInt(data.crime_count_total), icon: 'lock' },
    crime_rate_per_1000: { label: "Taux / 1000 hab.", value: data.crime_rate_per_1000 != null ? `${data.crime_rate_per_1000?.toFixed(1)}` : null, icon: 'trending_down' },
  };

  const categories = [
    {
      title: "Logement & Immobilier",
      icon: "home",
      keys: ["median_price", "nombre_logements_sociaux", "pct_eligible_ftth"],
      bgColor: "bg-blue-50/50 border-blue-100/70",
      textColor: "text-blue-700"
    },
    {
      title: "Mobilité & Transports",
      icon: "directions_bike",
      keys: ["station_count_velib", "avg_bikes_available", "metro_count", "rer_count", "tram_count", "bus_count"],
      bgColor: "bg-emerald-50/50 border-emerald-100/70",
      textColor: "text-emerald-700"
    },
    {
      title: "Nature & Cadre de vie",
      icon: "eco",
      keys: ["park_count", "nb_ilots_fraicheur", "arbres_per_km2", "european_aqi", "pollen_risk"],
      bgColor: "bg-teal-50/50 border-teal-100/70",
      textColor: "text-teal-700"
    },
    {
      title: "Commerce & Quartier",
      icon: "storefront",
      keys: ["restaurant_count", "bar_count", "shop_count"],
      bgColor: "bg-amber-50/50 border-amber-100/70",
      textColor: "text-amber-700"
    },
    {
      title: "Sécurité & Prévention",
      icon: "shield",
      keys: ["crime_count_total", "crime_rate_per_1000"],
      bgColor: "bg-rose-50/50 border-rose-100/70",
      textColor: "text-rose-700"
    }
  ];

  return (
    <div className="flex flex-col gap-3">
      {categories.map((cat) => {
        const activeRows = cat.keys
          .map((k) => allMetrics[k])
          .filter((m) => m && m.value !== null && m.value !== '—' && m.value !== undefined);

        if (activeRows.length === 0) return null;

        return (
          <div key={cat.title} className={`card p-3 border ${cat.bgColor}`}>
            <p className={`text-[10px] font-bold ${cat.textColor} uppercase tracking-wider mb-2 flex items-center gap-1.5`}>
              <span className="material-icon text-sm">{cat.icon}</span>
              {cat.title}
            </p>
            <div className="grid grid-cols-2 gap-y-2 gap-x-3">
              {activeRows.map(({ label, value, icon }) => (
                <div key={label} className="flex items-center gap-1.5 min-w-0">
                  <span className="material-icon text-base text-slate-400 shrink-0">{icon}</span>
                  <div className="min-w-0">
                    <p className="text-[9px] text-slate-500 leading-tight truncate">{label}</p>
                    <p className="text-xs font-bold text-slate-800 leading-normal mt-0.5">{value}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
