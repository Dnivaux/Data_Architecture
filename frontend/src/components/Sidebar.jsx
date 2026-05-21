import LiveStatusBadge from './LiveStatusBadge';

export const INDICATOR_OPTIONS = [
  { id: 'livability_score',   label: 'Vivabilité composite', icon: '🏙️' },
  { id: 'connectivity_score', label: 'Connectivité',          icon: '📡' },
  { id: 'mobility_score',     label: 'Mobilité',              icon: '🚲' },
  { id: 'health_env_score',   label: 'Santé Environnementale',icon: '🌿', desc: 'Végétalisation, air pur, îlots de fraîcheur' },
  { id: 'tranquility_score',  label: 'Tranquillité',          icon: '🔕', desc: 'Sécurité, peu de dynamisme nocturne' },
  { id: 'anime_score',        label: 'Animation',             icon: '🎭' },
  { id: 'calme_score',        label: 'Calme',                 icon: '😌', desc: 'Absence de bruit (Lden)' },
  { id: 'median_price',       label: 'Prix m² médian',        icon: '💶' },
];

/**
 * Sidebar gauche : filtres globaux + statistiques Paris + statut live.
 */
export default function Sidebar({
  selectedIndicator,
  onIndicatorChange,
  liveMetrics,
  globalStats,
  showChantiers,
  onToggleChantiers,
}) {
  return (
    <aside className="w-64 shrink-0 flex flex-col gap-5 bg-slate-800/60 border-r border-slate-700 px-4 py-5 overflow-y-auto">

      {/* Logo / titre */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xl">🏙️</span>
          <span className="font-bold text-slate-100 text-sm tracking-tight">
            Urban Data Explorer
          </span>
        </div>
        <p className="text-xs text-slate-500 pl-7">Paris · Architecture Medallion</p>
      </div>

      <hr className="border-slate-700" />

      {/* Indicateur thématique */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-2">
          Indicateur affiché
        </p>
        <nav className="flex flex-col gap-1">
          {INDICATOR_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              onClick={() => onIndicatorChange(opt.id)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors text-left w-full
                ${selectedIndicator === opt.id
                  ? 'bg-indigo-600/30 text-indigo-300 font-medium border border-indigo-500/30'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-slate-700/50'
                }`}
            >
              <span>{opt.icon}</span>
              <span className="leading-tight">{opt.label}</span>
            </button>
          ))}
        </nav>
      </div>

      <hr className="border-slate-700" />

      {/* Statistiques globales Paris */}
      {globalStats && (
        <div>
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-2">
            Statistiques Paris
          </p>
          <div className="flex flex-col gap-2">
            <StatRow label="Vivabilité moy." value={globalStats.avgLivability} unit="/ 100" />
            <StatRow label="Prix médian moy." value={globalStats.avgPrice} unit="€/m²" />
            <StatRow label="Meilleur arrdt" value={`${globalStats.bestArr}e`} />
            <StatRow label="Moins cher" value={`${globalStats.cheapestArr}e`} />
          </div>
        </div>
      )}

      <hr className="border-slate-700" />

      {/* Couches cartographiques */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-2">
          Couches
        </p>
        <button
          onClick={onToggleChantiers}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm w-full text-left transition-colors
            ${showChantiers
              ? 'bg-orange-600/30 text-orange-300 font-medium border border-orange-500/30'
              : 'text-slate-400 hover:text-slate-100 hover:bg-slate-700/50'
            }`}
        >
          <span>🚧</span>
          <span className="leading-tight">Chantiers</span>
          {showChantiers && (
            <span className="ml-auto text-[10px] bg-orange-500/20 text-orange-300 px-1.5 py-0.5 rounded-full">
              LIVE
            </span>
          )}
        </button>
      </div>

      <div className="mt-auto">
        <hr className="border-slate-700 mb-4" />
        {/* Statut micro-batch */}
        <div className="card">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-2">
            Micro-batch Vélib'
          </p>
          <LiveStatusBadge
            isLive={liveMetrics?.isLive}
            lastUpdate={liveMetrics?.lastUpdate}
            label="Flux actif"
          />
          {liveMetrics?.isLive && liveMetrics.data && (
            <p className="text-xs text-slate-500 mt-1.5">
              Polling · toutes les 30 s
            </p>
          )}
        </div>
      </div>
    </aside>
  );
}

function StatRow({ label, value, unit }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-xs font-medium text-slate-300">
        {value}{unit ? <span className="text-slate-500 ml-0.5">{unit}</span> : null}
      </span>
    </div>
  );
}
