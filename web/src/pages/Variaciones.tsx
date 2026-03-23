import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchVariaciones, type VariacionItem } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { DataTable } from '../components/tables/DataTable';
import { VariationBarChart } from '../components/charts/VariationBarChart';
import { formatCurrency } from '../lib/constants';

const col = createColumnHelper<VariacionItem>();
const columns = [
  col.accessor('productos_descripcion', { header: 'Producto' }),
  col.accessor('productos_marca', { header: 'Marca' }),
  col.accessor('provincia', { header: 'Provincia' }),
  col.accessor('precio_anterior', {
    header: 'Precio anterior',
    cell: (info) => info.getValue() != null ? formatCurrency(info.getValue()!) : '—',
  }),
  col.accessor('precio_actual', {
    header: 'Precio actual',
    cell: (info) => info.getValue() != null ? formatCurrency(info.getValue()!) : '—',
  }),
  col.accessor('variacion', {
    header: 'Variacion %',
    cell: (info) => {
      const v = info.getValue();
      if (v == null) return '—';
      return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
    },
  }),
];

export function Variaciones() {
  const { provincia, cadenas } = useFilters();
  const [mode, setMode] = useState<'lista' | 'promo'>('lista');

  const { data, isLoading } = useQuery({
    queryKey: ['variaciones', mode, provincia, cadenas],
    queryFn: () =>
      fetchVariaciones({
        mode,
        provincia: provincia || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
      }),
  });

  const items = data?.items ?? [];
  const chartData = items
    .filter((i) => i.variacion != null)
    .map((i) => ({
      label: `${(i.productos_descripcion || '').slice(0, 40)} (${(i.provincia || '').slice(0, 10)})`,
      value: i.variacion!,
    }));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Variaciones de Precios</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        <div className="space-y-4">
          <FilterPanel />
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">Tipo de precio</label>
            <div className="flex gap-2">
              {(['lista', 'promo'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                    mode === m
                      ? 'bg-slate-900 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {m === 'lista' ? 'Precio de lista' : 'Precio promo'}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          {data?.min_fecha && data?.max_fecha && (
            <p className="text-sm text-slate-500">
              Variaciones en los ultimos 7 dias ({data.min_fecha} a {data.max_fecha})
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {items.length > 0 && (
            <>
              <h2 className="text-lg font-semibold text-slate-800">
                Top {items.length} productos con mayor variacion
              </h2>
              <DataTable data={items} columns={columns} />
              <VariationBarChart data={chartData} />
            </>
          )}

          {!isLoading && items.length === 0 && data && (
            <p className="text-sm text-slate-400">No se encontraron variaciones para los filtros seleccionados.</p>
          )}
        </div>
      </div>
    </div>
  );
}
