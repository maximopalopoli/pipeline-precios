import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { COLORS } from '../../lib/constants';

interface VariationBarChartProps {
  data: { label: string; value: number }[];
  colorMode?: 'variation' | 'green';
}

export function VariationBarChart({ data, colorMode = 'variation' }: VariationBarChartProps) {
  const sorted = [...data].sort((a, b) => a.value - b.value);

  return (
    <ResponsiveContainer width="100%" height={Math.max(300, sorted.length * 32)}>
      <BarChart data={sorted} layout="vertical" margin={{ top: 5, right: 20, bottom: 5, left: 150 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis type="number" tick={{ fontSize: 12 }} />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fontSize: 11 }}
          width={140}
        />
        <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} />
        <Bar dataKey="value" barSize={20}>
          {sorted.map((entry, i) => (
            <Cell
              key={i}
              fill={
                colorMode === 'green'
                  ? '#2ecc71'
                  : entry.value > 0
                  ? COLORS.suba
                  : COLORS.baja
              }
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
