import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { useHousingTypology } from '../hooks/useHousingTypology';
import { fmtInt } from '../utils/formatters';

const TYPO_COLOR = '#2563EB';
const SURF_COLOR = '#10B981';

// Pourcentage à 1 décimale, sans « .0 » superflu (99.5 → "99.5", 100 → "100").
const pct1 = (v) => (v == null ? '0' : String(Math.round(v * 10) / 10));

const TYPO_LABELS = [
  { key: 'pct_t1', label: 'T1', hint: 'Studio / 1 pièce' },
  { key: 'pct_t2', label: 'T2', hint: '2 pièces' },
  { key: 'pct_t3', label: 'T3', hint: '3 pièces' },
  { key: 'pct_t4', label: 'T4', hint: '4 pièces' },
  { key: 'pct_t5p', label: 'T5+', hint: '5 pièces et +' },
];

const SURF_LABELS = [
  { key: 'pct_surf_lt30', label: '<30' },
  { key: 'pct_surf_30_50', label: '30-50' },
  { key: 'pct_surf_50_70', label: '50-70' },
  { key: 'pct_surf_70_100', label: '70-100' },
  { key: 'pct_surf_gte100', label: '100+' },
];

function MiniBar({ title, unit, rows }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <div>
      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">{title}</p>
      <ResponsiveContainer width="100%" height={108}>
        <BarChart data={rows} margin={{ top: 12, right: 8, bottom: 0, left: 2 }}>
          <XAxis
            dataKey="label"
            tick={{ fill: '#475569', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis hide domain={[0, max * 1.15]} />
          <Tooltip
            cursor={{ fill: 'rgba(37,99,235,0.06)' }}
            contentStyle={{
              backgroundColor: '#FFFFFF', border: '1px solid #E2E8F0',
              borderRadius: 8, fontSize: 11, padding: '4px 8px',
            }}
            formatter={(v, _n, p) => [`${v} %${p?.payload?.hint ? ` · ${p.payload.hint}` : ''}`, p?.payload?.label]}
          />
          <Bar dataKey="value" radius={[3, 3, 0, 0]} label={{ position: 'top', fontSize: 9, fill: '#64748B', formatter: (v) => `${Math.round(v)}` }}>
            {rows.map((r) => <Cell key={r.label} fill={r.color} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Répartition du parc immobilier transigé (DVF) : typologie T1..T5+,
 * tranches de surface, type de bien et surface médiane.
 * Attendu consigne : « répartition du parc immobilier selon les types de
 * logements et les surfaces ».
 */
export default function HousingTypologyChart({ arrondissement }) {
  const { data, loading } = useHousingTypology(arrondissement);

  if (loading) {
    return (
      <div className="card">
        <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Répartition du parc</p>
        <p className="text-xs text-slate-400 animate-pulse">Chargement…</p>
      </div>
    );
  }
  if (!data || !data.nb_total) {
    return (
      <div className="card">
        <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">Répartition du parc</p>
        <p className="text-xs text-slate-400">Données DVF indisponibles</p>
      </div>
    );
  }

  const typoRows = TYPO_LABELS.map((t) => ({
    label: t.label, hint: t.hint, value: data[t.key] ?? 0, color: TYPO_COLOR,
  }));
  const surfRows = SURF_LABELS.map((s) => ({
    label: s.label, value: data[s.key] ?? 0, color: SURF_COLOR,
  }));

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-slate-500 uppercase tracking-wide flex items-center gap-1.5">
          <span className="material-icon text-base text-blue-600">apartment</span>
          Répartition du parc
        </p>
        <span className="text-[10px] text-slate-400">{fmtInt(data.nb_total)} transactions DVF</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <MiniBar title="Typologie (pièces)" rows={typoRows} />
        <MiniBar title="Surface (m²)" rows={surfRows} />
      </div>

      {/* Stats synthèse : appartement / maison + surface médiane */}
      <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-slate-100">
        <SynthStat label="Appartements" value={`${pct1(data.pct_appartement)} %`} />
        <SynthStat label="Maisons" value={`${pct1(data.pct_maison)} %`} />
        <SynthStat label="Surface médiane" value={data.median_surface != null ? `${Math.round(data.median_surface)} m²` : '—'} />
      </div>
    </div>
  );
}

function SynthStat({ label, value }) {
  return (
    <div className="bg-slate-50 border border-slate-150 rounded-lg p-2 text-center">
      <p className="text-[9px] text-slate-400 uppercase leading-tight">{label}</p>
      <p className="text-sm font-semibold text-slate-800 mt-0.5">{value}</p>
    </div>
  );
}
