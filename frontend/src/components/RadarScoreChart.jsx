import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip, Legend,
} from 'recharts';

const SCORE_AXES = [
  { key: 'anime_score',         label: 'Animation'    },
  { key: 'tranquility_score',   label: 'Tranquillité' },
  { key: 'connectivity_score',  label: 'Connectivité' },
  { key: 'mobility_score',      label: 'Mobilité'     },
  { key: 'health_env_score',    label: 'Santé Env.'   },
];

function buildData(score) {
  return SCORE_AXES.map(({ key, label }) => ({
    subject: label,
    value: score?.[key] != null ? +score[key].toFixed(1) : 0,
    fullMark: 100,
  }));
}

/**
 * Radar chart comparant les 7 scores d'un (ou deux) arrondissement(s).
 *
 * Props :
 *   primary   : ArrondissementScore — arrondissement sélectionné
 *   secondary : ArrondissementScore — arrondissement comparé (optionnel)
 *   labelA    : string
 *   labelB    : string
 */
export default function RadarScoreChart({ primary, secondary, labelA = 'Sélectionné', labelB = 'Comparé' }) {
  if (!primary) return <EmptyState />;

  const dataA = buildData(primary);
  const dataB = secondary ? buildData(secondary) : null;

  // Merge pour que recharts lise un seul tableau
  const merged = dataA.map((d, i) => ({
    subject: d.subject,
    [labelA]: d.value,
    ...(dataB ? { [labelB]: dataB[i].value } : {}),
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <RadarChart data={merged} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
        <PolarGrid stroke="#D0D7DE" />
        <PolarAngleAxis
          dataKey="subject"
          tick={{ fill: '#64748B', fontSize: 11 }}
        />
        <PolarRadiusAxis
          angle={90}
          domain={[0, 100]}
          tick={{ fill: '#64748B', fontSize: 9 }}
          tickCount={4}
        />
        <Radar
          name={labelA}
          dataKey={labelA}
          stroke="#0F4C81"
          fill="#0F4C81"
          fillOpacity={0.35}
          strokeWidth={2}
        />
        {dataB && (
          <Radar
            name={labelB}
            dataKey={labelB}
            stroke="#2EC4B6"
            fill="#2EC4B6"
            fillOpacity={0.25}
            strokeWidth={2}
          />
        )}
        <Tooltip
          contentStyle={{ backgroundColor: '#F4F6F9', border: '1px solid #D0D7DE', borderRadius: 8 }}
          labelStyle={{ color: '#1E293B', fontSize: 12 }}
          itemStyle={{ color: '#64748B', fontSize: 12 }}
          formatter={(v) => [`${v} / 100`]}
        />
        {dataB && <Legend wrapperStyle={{ fontSize: 12, color: '#64748B' }} />}
      </RadarChart>
    </ResponsiveContainer>
  );
}

function EmptyState() {
  return (
    <div className="h-64 flex items-center justify-center text-[#64748B] text-sm">
      Cliquez sur un arrondissement pour afficher son profil
    </div>
  );
}
