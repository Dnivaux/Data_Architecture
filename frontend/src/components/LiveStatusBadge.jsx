import { fmtRelative } from '../utils/formatters';

/**
 * Badge "point vert clignotant" pour signaler un flux de données actif.
 * Utilisé dans le header et les KPI Cards de mobilité.
 */
export default function LiveStatusBadge({ isLive, lastUpdate, label = 'Temps réel' }) {
  return (
    <div className="flex items-center gap-2">
      <span className="relative flex h-2 w-2">
        {isLive && (
          <span className="animate-ping-slow absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75" />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${
            isLive ? 'bg-[#10B981]' : 'bg-slate-400'
          }`}
        />
      </span>
      <span className="text-xs text-slate-500">
        {isLive ? (
          <>
            <span className="text-[#10B981] font-medium">{label}</span>
            {lastUpdate && (
              <span className="ml-1 text-slate-500">· {fmtRelative(lastUpdate)}</span>
            )}
          </>
        ) : (
          <span className="text-slate-500">Hors ligne</span>
        )}
      </span>
    </div>
  );
}
