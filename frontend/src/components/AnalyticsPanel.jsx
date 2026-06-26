import { useState, useEffect, useMemo } from 'react';
import RadarScoreChart, { IRIS_SCORE_AXES } from './RadarScoreChart';
import PriceLineChart from './PriceLineChart';
import HousingTypologyChart from './HousingTypologyChart';
import { usePrices } from '../hooks/usePrices';
import { useOperators } from '../hooks/useOperators';
import {
  fmtArrondissement, fmtInt, fmtPrice, fmtEur, fmtAffordability,
  computeAffordability, ARRONDISSEMENT_NAMES,
} from '../utils/formatters';
import { api } from '../api/client';

/**
 * Panneau d'analyse (droite du dashboard, 40%).
 * Affiche : Radar des scores + évolution des prix + métriques brutes.
 * En mode comparaison, superpose 2 arrondissements sur le radar.
 *
 * Mode quartier (IRIS) : quand un quartier est sélectionné sur la carte, le
 * panneau bascule sur ce quartier (prix DVF, scores) et permet de le comparer
 * à un autre quartier du même arrondissement.
 */
export default function AnalyticsPanel({
  selectedArrondissement, indicatorData, scoreData,
  iris, selectedIris, onSelectIris,
}) {
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

  // ── Mode quartier (IRIS) ─────────────────────────────────────
  const irisList = useMemo(
    () => (selectedArrondissement
      ? (iris ?? []).filter((d) => d.arrondissement === selectedArrondissement)
      : []),
    [iris, selectedArrondissement],
  );
  const selectedIrisData = useMemo(
    () => irisList.find((d) => d.code_iris === selectedIris) ?? null,
    [irisList, selectedIris],
  );
  const [compareIris, setCompareIris] = useState('');
  useEffect(() => { setCompareIris(''); }, [selectedIris, selectedArrondissement]);
  const compareIrisData = compareIris
    ? irisList.find((d) => d.code_iris === compareIris) ?? null
    : null;

  if (selectedIrisData) {
    return (
      <IrisPanel
        irisList={irisList}
        primary={selectedIrisData}
        secondary={compareIrisData}
        compareIris={compareIris}
        onCompareChange={(e) => setCompareIris(e.target.value)}
        onClear={() => onSelectIris?.(null)}
        arrondissement={selectedArrondissement}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto pr-1">

      {/* Titre */}
      <div>
        <h2 className="text-sm font-semibold text-slate-800">
          {name ?? 'Vue globale — Paris'}
        </h2>
        <p className="text-xs text-slate-500 mt-0.5">
          {selectedArrondissement
            ? `${fmtArrondissement(selectedArrondissement)} · Cliquez un quartier sur la carte pour le détail`
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

      {/* Accessibilité logement : prix DVF mis en relation avec le revenu INSEE */}
      <AccessibilityCard data={scoreData} />

      {/* Répartition du parc immobilier (typologie + surfaces) — DVF */}
      <HousingTypologyChart arrondissement={selectedArrondissement || 0} />

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
// Carte Accessibilité — met en relation prix DVF et revenu médian INSEE
// (attendu consigne : « mesures d'accessibilité prix/loyers vs revenus »)
// ─────────────────────────────────────────────────────────────────
function AccessibilityCard({ data }) {
  if (!data) return null;
  const price = data.median_price;
  const income = data.median_income;
  const afford = data.affordability ?? computeAffordability(income, price);
  if (price == null && income == null && afford == null) return null;

  // Effort : nombre d'années de revenu médian pour acquérir un appartement
  // « type » de 50 m² (lecture concrète de l'accessibilité).
  const effortYears =
    price != null && income ? (price * 50) / income : null;

  return (
    <div className="card border border-indigo-100/70 bg-indigo-50/40">
      <p className="text-xs text-slate-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
        <span className="material-icon text-base text-indigo-600">real_estate_agent</span>
        Accessibilité au logement
      </p>
      <div className="grid grid-cols-3 gap-2">
        <AccessStat label="Prix médian" value={fmtPrice(price)} color="#4F46E5" />
        <AccessStat label="Revenu médian" value={income != null ? `${fmtEur(income)}/an` : '—'} color="#4F46E5" />
        <AccessStat label="m² / an de revenu" value={fmtAffordability(afford)} color="#059669" />
      </div>
      {effortYears != null && (
        <p className="text-[11px] text-slate-500 mt-2.5 leading-snug">
          <span className="font-semibold text-slate-700">{effortYears.toFixed(1)} ans</span> de
          revenu médian pour acquérir un 50 m² · plus le ratio « m²/an » est élevé,
          plus le logement est accessible.
        </p>
      )}
    </div>
  );
}

function AccessStat({ label, value, color }) {
  return (
    <div className="bg-white/70 border border-slate-150 rounded-lg p-2 text-center">
      <p className="text-[9px] text-slate-400 uppercase leading-tight">{label}</p>
      <p className="text-sm font-semibold mt-0.5" style={{ color }}>{value}</p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Mode quartier (IRIS) — détail d'un quartier + comparaison intra-arrondissement
// ─────────────────────────────────────────────────────────────────
const _eurM2 = (v) =>
  v != null ? `${new Intl.NumberFormat('fr-FR').format(Math.round(v))} €/m²` : '—';
const _eur = (v) =>
  v != null ? `${new Intl.NumberFormat('fr-FR').format(Math.round(v))} €` : '—';

function IrisPanel({ irisList, primary, secondary, compareIris, onCompareChange, onClear, arrondissement }) {
  const labelA = primary.nom_iris ?? primary.code_iris;
  const labelB = secondary ? (secondary.nom_iris ?? secondary.code_iris) : undefined;

  return (
    <div className="flex flex-col gap-4 h-full overflow-y-auto pr-1">

      {/* Titre quartier */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
            <span className="material-icon text-base text-blue-600">grid_on</span>
            {labelA}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Quartier (IRIS {primary.code_iris}) · Paris {arrondissement}e
          </p>
        </div>
        <button className="btn-ghost text-xs shrink-0" onClick={onClear}>
          ← Arrondissement
        </button>
      </div>

      {/* Comparaison quartier ↔ quartier */}
      <div>
        <label className="text-xs text-slate-500 mb-1 block">Comparer avec un quartier</label>
        <select className="select-field" value={compareIris} onChange={onCompareChange}>
          <option value="">— Choisir un quartier —</option>
          {irisList
            .filter((d) => d.code_iris !== primary.code_iris)
            .map((d) => (
              <option key={d.code_iris} value={d.code_iris}>
                {d.nom_iris ?? d.code_iris}
              </option>
            ))}
        </select>
      </div>

      {/* Radar — axes IRIS (Animation, Tranquillité, Mobilité) */}
      <div className="card">
        <div className="flex items-start justify-between mb-1">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Profil des scores</p>
          <div className="flex items-center gap-3 shrink-0">
            <GlobalScoreBadge label={labelA} value={primary.livability_score} color="#2563EB" />
            {secondary && (
              <GlobalScoreBadge label={labelB} value={secondary.livability_score} color="#10B981" />
            )}
          </div>
        </div>
        <RadarScoreChart
          primary={primary}
          secondary={secondary}
          labelA={labelA}
          labelB={labelB}
          axes={IRIS_SCORE_AXES}
        />
      </div>

      {/* Prix DVF + revenu — au niveau quartier */}
      <div className="card">
        <p className="text-xs text-slate-500 uppercase tracking-wide mb-3">Prix &amp; revenus du quartier</p>
        <div className="grid grid-cols-2 gap-3">
          <IrisStat label="Prix m² médian (DVF)" value={_eurM2(primary.median_price)} color="#2563EB" sub={labelA} />
          {secondary && (
            <IrisStat label="Prix m² médian (DVF)" value={_eurM2(secondary.median_price)} color="#10B981" sub={labelB} />
          )}
          <IrisStat label="Revenu médian (INSEE)" value={_eur(primary.median_income)} color="#2563EB" sub={labelA} />
          {secondary && (
            <IrisStat label="Revenu médian (INSEE)" value={_eur(secondary.median_income)} color="#10B981" sub={labelB} />
          )}
        </div>
      </div>

      {/* Scores détaillés du quartier */}
      <div className="card">
        <p className="text-xs text-slate-500 uppercase tracking-wide mb-3">Scores du quartier</p>
        <div className="grid grid-cols-2 gap-y-2 gap-x-4">
          {IRIS_SCORE_AXES.concat([{ key: 'livability_score', label: 'Vivabilité' }]).map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between text-xs">
              <span className="text-slate-500">{label}</span>
              <span className="font-medium text-slate-800">
                {primary[key] != null ? `${primary[key].toFixed(1)}` : '—'}
                {secondary && (
                  <span className="text-emerald-600 ml-1.5">
                    / {secondary[key] != null ? secondary[key].toFixed(1) : '—'}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-slate-400 mt-2">
          Scores normalisés par rang sur les 992 IRIS de Paris (médiane ~50).
          Connectivité &amp; Santé Env. sont identiques dans tout l'arrondissement
          (sources non infra-communales) — exclues de la comparaison de quartiers.
        </p>
      </div>
    </div>
  );
}

function IrisStat({ label, value, color, sub }) {
  return (
    <div className="bg-slate-50 border border-slate-150 rounded-lg p-2">
      <p className="text-[10px] text-slate-400 uppercase leading-tight">{label}</p>
      <p className="text-sm font-semibold mt-0.5" style={{ color }}>{value}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5 truncate">{sub}</p>}
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
    median_income: { label: "Revenu médian INSEE", value: data.median_income != null ? fmtEur(data.median_income) : null, icon: 'euro' },
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
    nb_bars: { label: "Bars", value: fmtInt(data.nb_bars ?? data.bar_count), icon: 'local_bar' },
    cinema_count: { label: "Cinémas", value: fmtInt(data.cinema_count), icon: 'theaters' },
    nb_nightclubs: { label: "Boîtes de nuit", value: fmtInt(data.nb_nightclubs ?? data.nightclub_count), icon: 'nightlife' },
    museum_count: { label: "Musées", value: fmtInt(data.museum_count), icon: 'museum' },
    stadium_count: { label: "Stades & salles de sport", value: fmtInt(data.stadium_count), icon: 'stadium' },
    crime_count_total: { label: "Crimes & délits", value: fmtInt(data.crime_count_total), icon: 'lock' },
    crime_rate_per_1000: { label: "Taux / 1000 hab.", value: data.crime_rate_per_1000 != null ? `${data.crime_rate_per_1000?.toFixed(1)}` : null, icon: 'trending_down' },
  };

  const categories = [
    {
      title: "Logement & Immobilier",
      icon: "home",
      keys: ["median_price", "median_income", "nombre_logements_sociaux", "pct_eligible_ftth"],
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
      title: "Dynamisme & Sorties",
      icon: "nightlife",
      keys: ["restaurant_count", "nb_bars", "cinema_count", "nb_nightclubs", "museum_count", "stadium_count"],
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
