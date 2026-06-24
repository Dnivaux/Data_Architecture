import KPICard from './KPICard';
import { fmtPrice, fmtPct, fmtScoreShort, fmtInt, fmtEur } from '../utils/formatters';

/**
 * Grille de 7 KPI Cards affichées en haut du dashboard.
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
      id: 'median_price',
      icon: 'home',
      label: 'Prix médian',
      value: fmtPrice(d.median_price),
      subValue: 'Données DVF (dernière année)',
      score: null,
      'data-testid': 'kpi-price',
    },
    {
      id: 'median_income',
      icon: 'euro',
      label: 'Revenu médian',
      value: fmtEur(d.median_income),
      subValue: 'Données INSEE FiLoSoFi',
      score: null,
      'data-testid': 'kpi-income',
    },
    {
      id: 'livability_score',
      icon: 'insights',
      label: 'Score de vivabilité',
      value: fmtScoreShort(d.livability_score),
      subValue: 'Score global pondéré',
      score: d.livability_score,
      'data-testid': 'kpi-livability',
    },
    {
      id: 'mobility_score',
      icon: 'directions_bike',
      label: 'Mobilité',
      value: fmtScoreShort(d.mobility_score),
      subValue: d.station_count_velib != null
        ? `${fmtInt(d.station_count_velib)} stations Vélib'`
        : "Score Vélib' + PRIM",
      score: d.mobility_score,
      isLive: mobilityLive,
      'data-testid': 'kpi-mobility',
    },
    {
      id: 'anime_score',
      icon: 'theater_comedy',
      label: 'Dynamisme du quartier',
      value: fmtScoreShort(d.anime_score),
      subValue: 'Commerces, restaurants, bars',
      score: d.anime_score,
      'data-testid': 'kpi-dynamism',
    },
    {
      id: 'health_env_score',
      icon: 'eco',
      label: 'Santé Environnementale',
      value: fmtScoreShort(d.health_env_score),
      subValue: d.nb_ilots_fraicheur != null
        ? `${fmtInt(d.nb_ilots_fraicheur)} îlots de fraîcheur`
        : 'Végétalisation + îlots',
      score: d.health_env_score,
      'data-testid': 'kpi-health-env',
    },
    {
      id: 'tranquility_score',
      icon: 'shield',
      label: 'Tranquillité',
      value: fmtScoreShort(d.tranquility_score),
      subValue: 'Sécurité, peu de dynamisme nocturne',
      score: d.tranquility_score,
      'data-testid': 'kpi-tranquility',
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
      {kpis.map((kpi) => (
        <KPICard
          key={kpi.label}
          {...kpi}
          onClick={onIndicatorClick ? () => onIndicatorClick(kpi.id) : undefined}
        />
      ))}
    </div>
  );
}

function KPISkeleton({ loading = false }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
      {Array.from({ length: 7 }).map((_, i) => (
        <div key={i} className={`card ${loading ? 'animate-pulse' : ''}`}>
          <div className="h-3 bg-slate-200 rounded w-2/3 mb-3" />
          <div className="h-7 bg-slate-200 rounded w-1/2 mb-2" />
          <div className="h-2 bg-slate-200 rounded w-full" />
          {!loading && i === 0 && (
            <p className="text-xs text-slate-500 mt-2">Aucune donnée</p>
          )}
        </div>
      ))}
    </div>
  );
}
