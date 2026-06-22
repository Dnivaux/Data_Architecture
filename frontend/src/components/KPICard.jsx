import { scoreToTextClass, scoreToBgClass } from '../utils/scoreColors';

/**
 * Carte KPI individuelle.
 *
 * Props :
 *   icon        : emoji ou composant JSX affiché en haut à gauche
 *   label       : titre de la métrique
 *   value       : valeur formatée (string)
 *   subValue    : ligne secondaire (ex: "84/100", optionnel)
 *   score       : number 0-100 pour la barre de progression (null = pas de barre)
 *   isLive      : bool — active l'effet pulse Tailwind
 *   trend       : "+12%" ou "-3%" (optionnel)
 *   trendUp     : bool — vert si true, rouge si false
 *   onClick     : callback
 */
export default function KPICard({
  icon,
  label,
  value,
  subValue,
  score,
  isLive = false,
  trend,
  trendUp,
  onClick,
}) {
  const isClickable = Boolean(onClick);

  return (
    <div
      className={`card flex flex-col gap-3 relative overflow-hidden
        ${isClickable ? 'card-hover' : ''}
        ${isLive ? 'ring-1 ring-[#10B981]/40' : ''}`}
      onClick={onClick}
    >
      {/* Halo de fond pour les cartes live */}
      {isLive && (
        <div className="absolute inset-0 bg-[#10B981]/5 pointer-events-none" />
      )}

      {/* En-tête */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="material-icon text-xl text-slate-500">{icon}</span>
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wide">
            {label}
          </span>
        </div>

        {/* Badge LIVE */}
        {isLive && (
          <span className="relative flex h-2 w-2 mt-0.5">
            <span className="animate-ping-slow absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[#10B981]" />
          </span>
        )}
      </div>

      {/* Valeur principale */}
      <div className="flex items-end gap-2">
        <span className={`text-2xl font-bold ${score != null ? scoreToTextClass(score) : 'text-slate-800'}`}>
          {value}
        </span>
        {trend && (
          <span
            className={`text-xs font-medium pb-0.5 ${
              trendUp ? 'text-emerald-600' : 'text-rose-600'
            }`}
          >
            {trend}
          </span>
        )}
      </div>

      {/* Sous-valeur */}
      {subValue && (
        <p className="text-xs text-slate-500 -mt-2">{subValue}</p>
      )}

      {/* Barre de score */}
      {score != null && (
        <div className="score-bar-track">
          <div
            className={`h-full rounded-full transition-all duration-700 ${scoreToBgClass(score)}`}
            style={{ width: `${Math.max(2, score)}%` }}
          />
        </div>
      )}
    </div>
  );
}
