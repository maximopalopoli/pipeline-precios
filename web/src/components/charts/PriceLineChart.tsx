import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { COLORS, formatCurrency } from '../../lib/constants';

interface PriceLineChartProps {
  data: { fecha: string; precio_lista: number | null; precio_promo: number | null }[];
}

export function PriceLineChart({ data }: PriceLineChartProps) {
  return (
    <ResponsiveContainer width="100%" height={400}>
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="fecha" tick={{ fontSize: 12 }} />
        <YAxis tickFormatter={(v) => `$${v}`} tick={{ fontSize: 12 }} />
        <Tooltip
          formatter={(value, name) => [
            formatCurrency(Number(value)),
            name === 'precio_lista' ? 'Precio lista' : 'Precio promo',
          ]}
        />
        <Legend
          formatter={(value) =>
            value === 'precio_lista' ? 'Precio lista' : 'Precio promo'
          }
        />
        <Line
          type="monotone"
          dataKey="precio_lista"
          stroke={COLORS.precioLista}
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="precio_promo"
          stroke={COLORS.precioPromo}
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
