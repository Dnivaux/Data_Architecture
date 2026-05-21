import KPICard from './KPICard';
import { fmtPrice, fmtPct, fmtScoreShort, fmtInt } from '../utils/formatters';

/**
 * Grille de 6 KPI Cards affichées en haut du dashboard.
 *
 * Si `data` est null, affiche des squelettes de chargement.
 * Si `liveData` est fourni, les cartes Mobilité pulsent en vert.
 */
export default function KPIGrid({ data, liveData, onIndicatorClick }) {
  if (!data) return <KPISkeleton loading={true} />;

  const d = data;
  const mobilityLive = liveData?.isLive ?? false;

  const kpis = [
    {
      icon: '🏠',
      label: 'Prix médian',
      value: fmtPrice(d.median_price),
      subValue: 'Données DVF (dernière année)',
      score: null,
    },
    {
      icon: '📊',
      label: 'Vivabilité composite',
      value: fmtScoreShort(d.livability_score),
      subValue: 'Score global pondéré',
      score: d.livability_score,
    },
    {
      icon: '🚲',
      label: 'Mobilité',
      value: fmtScoreShort(d.mobility_score),
      subValue: d.station_count_velib != null
        ? `${fmtInt(d.station_count_velib)} stations Vélib'`
        : 'Score Vélib\' + PRIM',
      score: d.mobility_score,
      isLive: mobilityLive,
    },
    {
      icon: '📡',
      label: 'Connectivité',
      value: fmtScoreShort(d.connectivity_score),
      subValue: d.pct_eligible_ftth != null
        ? `${Math.round(d.pct_eligible_ftth)} % éligibles fibre`
        : 'Fibre + 4G/5G',
      score: d.connectivity_score,
    },
    {
      icon: '🌿',
      label: 'Santé Environnementale',
      value: fmtScoreShort(d.health_env_score),
      subValue: d.nb_ilots_fraicheur != null
        ? `${fmtInt(d.nb_ilots_fraicheur)} îlots de fraîcheur`
        : 'Végétalisation + îlots',
      score: d.health_env_score,
    },
    {
      icon: '🏛️',
      label: 'Logements sociaux',
      value: fmtPct(d.social_housing_pct),
      subValue: `${fmtInt(d.bar_count)} bars · ${fmtInt(d.park_count)} parcs`,
      score: null,
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {kpis.map((kpi) => (
        <KPICard
          key={kpi.label}
          {...kpi}
          onClick={onIndicatorClick ? () => onIndicatorClick(kpi.label) : undefined}
        />
      ))}
    </div>
  );
}

function KPISkeleton({ loading = false }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className={`card ${loading ? 'animate-pulse' : ''}`}>
          <div className="h-3 bg-slate-700 rounded w-2/3 mb-3" />
          <div className="h-7 bg-slate-700 rounded w-1/2 mb-2" />
          <div className="h-2 bg-slate-700 rounded w-full" />
          {!loading && i === 0 && (
            <p className="text-xs text-slate-600 mt-2">Aucune donnée</p>
          )}
        </div>
      ))}
    </div>
  );
}
