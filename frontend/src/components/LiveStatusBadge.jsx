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
          <span className="animate-ping-slow absolute inline-flex h-full w-full rounded-full bg-[#2EC4B6] opacity-75" />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${
            isLive ? 'bg-[#2EC4B6]' : 'bg-[#64748B]'
          }`}
        />
      </span>
      <span className="text-xs text-[#64748B]">
        {isLive ? (
          <>
            <span className="text-[#2EC4B6] font-medium">{label}</span>
            {lastUpdate && (
              <span className="ml-1 text-[#64748B]">· {fmtRelative(lastUpdate)}</span>
            )}
          </>
        ) : (
          <span className="text-[#64748B]">Hors ligne</span>
        )}
      </span>
    </div>
  );
}
