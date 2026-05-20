import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { fmtInt } from '../utils/formatters';

/** Graphique linéaire de l'évolution du prix médian DVF (€/m²) par année. */
export default function PriceLineChart({ prices, loading }) {
  if (loading) {
    return (
      <div className="h-40 flex items-center justify-center">
        <span className="text-slate-500 text-sm animate-pulse">Chargement des prix…</span>
      </div>
    );
  }

  if (!prices || prices.length === 0) {
    return (
      <div className="h-40 flex items-center justify-center text-slate-500 text-sm">
        Aucune donnée DVF disponible
      </div>
    );
  }

  const sorted = [...prices].sort((a, b) => a.year - b.year);

  // Calcul de la tendance (dernière vs première valeur)
  const first = sorted.find((p) => p.median_price != null);
  const last = [...sorted].reverse().find((p) => p.median_price != null);
  const trend = first && last && first !== last
    ? (((last.median_price - first.median_price) / first.median_price) * 100).toFixed(1)
    : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-400 uppercase tracking-wide">Prix médian DVF</span>
        {trend && (
          <span className={`text-xs font-medium ${+trend >= 0 ? 'text-red-400' : 'text-emerald-400'}`}>
            {+trend >= 0 ? '+' : ''}{trend} % depuis {first?.year}
          </span>
        )}
      </div>

      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={sorted} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
          <XAxis
            dataKey="year"
            tick={{ fill: '#64748B', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: '#64748B', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${fmtInt(v)} €`}
            width={64}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1E293B',
              border: '1px solid #334155',
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: '#F1F5F9' }}
            itemStyle={{ color: '#06B6D4' }}
            formatter={(v) => [`${fmtInt(v)} €/m²`, 'Prix médian']}
          />
          <Line
            type="monotone"
            dataKey="median_price"
            stroke="#06B6D4"
            strokeWidth={2}
            dot={{ fill: '#06B6D4', r: 3 }}
            activeDot={{ r: 5 }}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
