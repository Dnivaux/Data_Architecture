import { useEffect, useRef, useState } from 'react';

/**
 * Slider temporel qui « rejoue » l'évolution historique sur la carte.
 * Contrôlé : `value` (année courante) + `onChange(année)`.
 * Bouton lecture ▶ : anime automatiquement année par année (boucle).
 *
 * Attendu consigne : « une timeline permettant de rejouer l'évolution
 * historique des tendances ».
 */
export default function TimelineSlider({ years, value, onChange, label = 'Année' }) {
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef(null);

  // Index courant dans le tableau d'années
  const idx = Math.max(0, years.indexOf(value));

  // Boucle de lecture : avance d'une année toutes les 1,1 s (revient au début
  // après la dernière). L'effet se recrée à chaque changement d'année → le
  // calcul de l'année suivante part toujours de la valeur courante.
  useEffect(() => {
    if (!playing || years.length < 2) return undefined;
    timerRef.current = setTimeout(() => {
      const cur = years.indexOf(value);
      const next = cur + 1 >= years.length ? 0 : cur + 1;
      onChange(years[next]);
    }, 1100);
    return () => clearTimeout(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, value, years]);

  // Arrêt auto de la lecture à la dernière année (sauf relance manuelle).
  useEffect(() => {
    if (playing && idx === years.length - 1) {
      const t = setTimeout(() => setPlaying(false), 1100);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [playing, idx, years.length]);

  if (!years || years.length === 0) return null;

  const min = years[0];
  const max = years[years.length - 1];

  return (
    <div className="flex items-center gap-3 bg-white/95 border border-slate-200 rounded-lg px-3 py-2 shadow-sm backdrop-blur-sm">
      <button
        onClick={() => setPlaying((p) => !p)}
        className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white transition-colors"
        style={{ backgroundColor: playing ? '#1D4ED8' : '#2563EB' }}
        title={playing ? 'Pause' : "Rejouer l'évolution"}
        aria-label={playing ? 'Pause' : 'Lecture'}
      >
        <span className="material-icon" style={{ fontSize: 18 }}>
          {playing ? 'pause' : 'play_arrow'}
        </span>
      </button>

      <div className="flex flex-col min-w-[150px]">
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
          <span className="text-sm font-bold text-blue-700 tabular-nums">{value}</span>
        </div>
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={value}
          onChange={(e) => { setPlaying(false); onChange(Number(e.target.value)); }}
          className="w-full accent-blue-600 cursor-pointer"
          list="timeline-years"
        />
        <datalist id="timeline-years">
          {years.map((y) => <option key={y} value={y} />)}
        </datalist>
        <div className="flex justify-between text-[10px] text-slate-400 tabular-nums">
          <span>{min}</span>
          <span>{max}</span>
        </div>
      </div>
    </div>
  );
}
