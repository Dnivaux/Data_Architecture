import LiveStatusBadge from './LiveStatusBadge';

export const INDICATOR_OPTIONS = [
  { id: 'livability_score',   label: 'Score de vivabilité',    icon: 'insights' },
  { id: 'connectivity_score', label: 'Connectivité',          icon: 'wifi' },
  { id: 'mobility_score',     label: 'Mobilité',              icon: 'directions_bike' },
  { id: 'health_env_score',   label: 'Santé Environnementale',icon: 'eco', desc: 'Végétalisation, air pur, îlots de fraîcheur' },
  { id: 'tranquility_score',  label: 'Tranquillité',          icon: 'shield', desc: 'Sécurité, peu de dynamisme nocturne' },
  { id: 'anime_score',        label: 'Dynamisme du quartier', icon: 'theater_comedy' },
  { id: 'calme_score',        label: 'Calme',                 icon: 'volume_off', desc: 'Absence de bruit (Lden)' },
  { id: 'median_price',       label: 'Prix m² médian',        icon: 'payments' },
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
    <aside className="w-64 shrink-0 flex flex-col gap-5 bg-[#EAEFF5] border-r border-[#D0D7DE] px-4 py-5 overflow-y-auto">

      {/* Logo / titre */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="material-icon text-xl">location_city</span>
          <span className="font-bold text-[#1E293B] text-sm tracking-tight">
            Urban Data Explorer
          </span>
        </div>
        <p className="text-xs text-[#64748B] pl-7">Paris</p>
      </div>

      <hr className="border-[#D0D7DE]" />

      {/* Indicateur thématique */}
      <div>
        <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
          Indicateur affiché
        </p>
        <nav className="flex flex-col gap-1">
          {INDICATOR_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              onClick={() => onIndicatorChange(opt.id)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors text-left w-full
                ${selectedIndicator === opt.id
                  ? 'bg-[#2EC4B6]/20 text-[#0F4C81] font-medium border border-[#2EC4B6]/40'
                  : 'text-[#64748B] hover:text-[#1E293B] hover:bg-[#E2E8F0]'
                }`}
            >
              <span className="material-icon text-[18px]">{opt.icon}</span>
              <span className="leading-tight">{opt.label}</span>
            </button>
          ))}
        </nav>
      </div>

      <hr className="border-[#D0D7DE]" />

      {/* Statistiques globales Paris */}
      {globalStats && (
        <div>
          <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
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

      <hr className="border-[#D0D7DE]" />

      {/* Couches cartographiques */}
      <div>
        <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
          Couches
        </p>
        <button
          onClick={onToggleChantiers}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm w-full text-left transition-colors
            ${showChantiers
              ? 'bg-[#2EC4B6]/20 text-[#0F4C81] font-medium border border-[#2EC4B6]/40'
              : 'text-[#64748B] hover:text-[#1E293B] hover:bg-[#E2E8F0]'
            }`}
        >
          <span className="material-icon text-[18px]">construction</span>
          <span className="leading-tight">Chantiers</span>
          {showChantiers && (
            <span className="ml-auto text-[10px] bg-[#2EC4B6]/20 text-[#0F4C81] px-1.5 py-0.5 rounded-full">
              LIVE
            </span>
          )}
        </button>
      </div>

      <div className="mt-auto">
        <hr className="border-[#D0D7DE] mb-4" />
        {/* Statut micro-batch */}
        <div className="card">
          <p className="text-xs font-semibold text-[#64748B] uppercase tracking-widest mb-2">
            Micro-batch Vélib'
          </p>
          <LiveStatusBadge
            isLive={liveMetrics?.isLive}
            lastUpdate={liveMetrics?.lastUpdate}
            label="Flux actif"
          />
          {liveMetrics?.isLive && liveMetrics.data && (
            <p className="text-xs text-[#64748B] mt-1.5">
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
      <span className="text-xs text-[#64748B]">{label}</span>
      <span className="text-xs font-medium text-[#1E293B]">
        {value}{unit ? <span className="text-[#64748B] ml-0.5">{unit}</span> : null}
      </span>
    </div>
  );
}
