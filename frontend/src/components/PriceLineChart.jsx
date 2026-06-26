import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { fmtInt } from '../utils/formatters';

const COLOR_A = '#2563EB';   // série principale (idem radar primary)
const COLOR_B = '#10B981';   // série comparée   (idem radar secondary)

/** Tendance (%) entre la première et la dernière valeur non nulle d'une série. */
function computeTrend(series) {
  const sorted = [...series].sort((a, b) => a.year - b.year);
  const first = sorted.find((p) => p.median_price != null);
  const last = [...sorted].reverse().find((p) => p.median_price != null);
  if (!first || !last || first === last) return null;
  return {
    pct: (((last.median_price - first.median_price) / first.median_price) * 100).toFixed(1),
    year: first.year,
  };
}

/**
 * Graphique linéaire de l'évolution du prix médian DVF (€/m²) par année.
 * En mode comparaison, superpose une seconde série (`comparePrices`).
 */
export default function PriceLineChart({
  prices,
  comparePrices = null,
  labelA = 'Sélectionné',
  labelB = 'Comparé',
  loading,
}) {
  if (loading) {
    return (
      <div className="h-40 flex items-center justify-center">
        <span className="text-[#64748B] text-sm animate-pulse">Chargement des prix…</span>
      </div>
    );
  }

  if (!prices || prices.length === 0) {
    return (
      <div className="h-40 flex items-center justify-center text-[#64748B] text-sm">
        Aucune donnée DVF disponible
      </div>
    );
  }

  const comparing = comparePrices && comparePrices.length > 0;

  // Fusion des deux séries sur l'année pour que recharts lise un seul tableau.
  const byYear = new Map();
  prices.forEach((p) => {
    byYear.set(p.year, { year: p.year, [labelA]: p.median_price ?? null });
  });
  if (comparing) {
    comparePrices.forEach((p) => {
      const row = byYear.get(p.year) ?? { year: p.year, [labelA]: null };
      row[labelB] = p.median_price ?? null;
      byYear.set(p.year, row);
    });
  }
  const merged = Array.from(byYear.values()).sort((a, b) => a.year - b.year);

  const trend = computeTrend(prices);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-slate-500 uppercase tracking-wide">Prix médian DVF</span>
        {trend && (
          <span className={`text-xs font-medium ${+trend.pct >= 0 ? 'text-[#2563EB]' : 'text-[#10B981]'}`}>
            {+trend.pct >= 0 ? '+' : ''}{trend.pct} % depuis {trend.year}
          </span>
        )}
      </div>

      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={merged} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
          <XAxis
            dataKey="year"
            tick={{ fill: '#475569', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: '#475569', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${fmtInt(v)} €`}
            width={64}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 8,
              fontSize: 12,
              boxShadow: '0 4px 6px -1px rgba(0,0,0,0.05)',
            }}
            labelStyle={{ color: '#0F172A' }}
            formatter={(v, name) => [`${fmtInt(v)} €/m²`, name]}
          />
          {comparing && <Legend wrapperStyle={{ fontSize: 12, color: '#475569' }} />}
          <Line
            type="monotone"
            dataKey={labelA}
            stroke={COLOR_A}
            strokeWidth={2}
            dot={{ fill: COLOR_B, r: 3 }}
            activeDot={{ r: 5 }}
            connectNulls
          />
          {comparing && (
            <Line
              type="monotone"
              dataKey={labelB}
              stroke={COLOR_B}
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={{ fill: COLOR_A, r: 3 }}
              activeDot={{ r: 5 }}
              connectNulls
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
