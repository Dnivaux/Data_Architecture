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
        <h2 className="text-sm font-semibold text-[#1E293B]">
          {name ?? 'Vue globale — Paris'}
        </h2>
        <p className="text-xs text-[#64748B] mt-0.5">
          {selectedArrondissement
            ? fmtArrondissement(selectedArrondissement)
            : '20 arrondissements · Sélectionnez un arrondissement sur la carte'}
        </p>
      </div>

      {/* Comparaison */}
      {selectedArrondissement && (
        <div>
          <label className="text-xs text-[#64748B] mb-1 block">Comparer avec</label>
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
        <p className="text-xs text-[#64748B] uppercase tracking-wide mb-1">
          Profil des scores
        </p>
        <RadarScoreChart
          primary={scoreData}
          secondary={comparisonScore}
          labelA={selectedArrondissement ? `Paris ${selectedArrondissement}e` : 'Paris (moy.)'}
          labelB={compareWith ? `Paris ${compareWith}e` : undefined}
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
            <span className="text-[#64748B] text-xs uppercase tracking-wide">Prix médian DVF</span>
            <span className="text-[#2EC4B6] text-xs text-center leading-relaxed">
              Données indisponibles<br />
              <span className="text-[#64748B]">(table Gold non peuplée)</span>
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
const OP_ICONS = {
  orange: { icon: 'fiber_manual_record', color: '#2EC4B6' },
  sfr: { icon: 'fiber_manual_record', color: '#0F4C81' },
  bouygues: { icon: 'fiber_manual_record', color: '#34D399' },
  free: { icon: 'fiber_manual_record', color: '#1F7A8C' },
};

function ConnectivityDetail({ data, loading }) {
  if (loading) {
    return (
      <div className="card">
        <p className="text-xs text-[#64748B] uppercase tracking-wide mb-2">Réseau</p>
        <p className="text-xs text-[#64748B] animate-pulse">Chargement…</p>
      </div>
    );
  }
  if (!data) return null;

  const { ftth_pct, best_4g, best_5g, operators = [] } = data;

  return (
    <div className="card">
      <p className="text-xs text-[#64748B] uppercase tracking-wide mb-3">Réseau &amp; Connectivité</p>

      {/* Meilleurs opérateurs */}
      <div className="flex gap-3 mb-3">
        {best_4g && (
          <div className="flex-1 bg-[#F4F6F9] border border-[#D0D7DE] rounded-lg p-2 text-center">
            <p className="text-[10px] text-[#64748B] uppercase">Meilleur 4G</p>
            <p className="text-xs font-semibold text-[#0F4C81] mt-0.5">{best_4g}</p>
          </div>
        )}
        {best_5g && (
          <div className="flex-1 bg-[#F4F6F9] border border-[#D0D7DE] rounded-lg p-2 text-center">
            <p className="text-[10px] text-[#64748B] uppercase">Meilleur 5G</p>
            <p className="text-xs font-semibold text-[#2EC4B6] mt-0.5">{best_5g}</p>
          </div>
        )}
        {ftth_pct != null && (
          <div className="flex-1 bg-[#F4F6F9] border border-[#D0D7DE] rounded-lg p-2 text-center">
            <p className="text-[10px] text-[#64748B] uppercase">Fibre</p>
            <p className="text-xs font-semibold text-[#34D399] mt-0.5">{ftth_pct} %</p>
          </div>
        )}
      </div>

      {/* Détail par opérateur (% de part d'antennes dans l'arrondissement) */}
      {operators.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] text-[#64748B] mb-1">Part des antennes par opérateur (source ARCEP 2025-T4)</p>
          {operators.map((op) => (
            <div key={op.operateur} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 text-[#1E293B] w-32">
                <span
                  className="material-icon"
                  style={{ color: OP_ICONS[op.operateur]?.color ?? '#0F4C81' }}
                >
                  {OP_ICONS[op.operateur]?.icon ?? 'signal_cellular_alt'}
                </span>
                {op.label}
              </span>
              <div className="flex gap-2">
                {op.pct_pop_4g != null && (
                  <span className="text-[#0F4C81]">4G&nbsp;{op.pct_pop_4g}%</span>
                )}
                {op.pct_pop_5g != null && (
                  <span className="text-[#2EC4B6]">5G&nbsp;{op.pct_pop_5g}%</span>
                )}
                {op.pct_pop_4g == null && op.pct_pop_5g == null && (
                  <span className="text-[#64748B]">—</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {operators.length === 0 && !best_4g && ftth_pct == null && (
        <p className="text-xs text-[#64748B]">Données ARCEP non disponibles — relancer le pipeline</p>
      )}
    </div>
  );
}

function MetricsDetail({ data }) {
  const rows = [
    { label: "Prix m² médian DVF", value: fmtPrice(data.median_price), icon: 'home' },
    { label: "Logements sociaux", value: fmtInt(data.nombre_logements_sociaux), icon: 'apartment' },
    { label: "Éligibles fibre", value: data.pct_eligible_ftth != null ? `${Math.round(data.pct_eligible_ftth)}%` : '—', icon: 'signal_wifi_4_bar' },
    { label: "Couv. 4G/5G (débit moy.)", value: data.avg_rate_dl_5g_mbps != null ? `${Math.round(data.avg_rate_dl_5g_mbps)} Mbps` : '—', icon: 'phone_iphone' },
    { label: "Stations Vélib'", value: fmtInt(data.station_count_velib), icon: 'directions_bike' },
    { label: 'Vélos dispos (moy.)',  value: data.avg_bikes_available != null ? `${data.avg_bikes_available?.toFixed(1)}` : '—', icon: 'sync' },
    { label: 'Stations métro', value: fmtInt(data.metro_count), icon: 'subway' },
    { label: 'Gares RER',      value: fmtInt(data.rer_count),   icon: 'train' },
    { label: 'Arrêts tram',    value: fmtInt(data.tram_count),  icon: 'tram' },
    { label: 'Arrêts bus',     value: fmtInt(data.bus_count),   icon: 'directions_bus' },
    { label: "Parcs & jardins", value: fmtInt(data.park_count), icon: 'park' },
    { label: 'Îlots de fraîcheur',  value: fmtInt(data.nb_ilots_fraicheur),     icon: 'ac_unit' },
    { label: 'Arbres / km²',        value: fmtInt(data.arbres_per_km2),         icon: 'forest' },
    { label: "Qualité de l'air (AQI)", value: data.european_aqi != null ? `${Math.round(data.european_aqi)}` : '—', icon: 'air' },
    { label: 'Risque pollen',       value: data.pollen_risk ?? '—',             icon: 'grass' },
    { label: "Restaurants", value: fmtInt(data.restaurant_count), icon: 'restaurant' },
    { label: "Bars", value: fmtInt(data.bar_count), icon: 'local_bar' },
    { label: "Commerces", value: fmtInt(data.shop_count), icon: 'storefront' },
    { label: 'Crimes & délits (total)',       value: fmtInt(data.crime_count_total),      icon: 'lock' },
    { label: 'Taux criminalité / 1000 hab.',   value: data.crime_rate_per_1000 != null ? `${data.crime_rate_per_1000?.toFixed(1)}` : '—', icon: 'trending_down' },
  ].filter((r) => r.value !== '—' && r.value !== 'undefined' && r.value != null);

  if (rows.length === 0) return null;

  return (
    <div className="card">
      <p className="text-xs text-[#64748B] uppercase tracking-wide mb-3">Métriques détaillées</p>
      <div className="grid grid-cols-2 gap-y-2 gap-x-4">
        {rows.map(({ label, value, icon }) => (
          <div key={label} className="flex items-center gap-1.5">
            <span className="material-icon text-base">{icon}</span>
            <div>
              <p className="text-xs text-[#64748B] leading-tight">{label}</p>
              <p className="text-sm font-medium text-[#1E293B]">{value}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ icon, label, value }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="material-icon text-base">{icon}</span>
      <div>
        <p className="text-xs text-[#64748B] leading-tight">{label}</p>
        <p className="text-sm font-medium text-[#1E293B]">{value}</p>
      </div>
    </div>
  );
}
