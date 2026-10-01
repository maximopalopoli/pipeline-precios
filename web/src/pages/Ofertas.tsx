import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchOfertas, type OfertaItem } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { DataTable } from '../components/tables/DataTable';
import { VariationBarChart } from '../components/charts/VariationBarChart';
import { KpiCard } from '../components/layout/KpiCard';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';
import { formatCurrency, formatPercent } from '../lib/constants';

const col = createColumnHelper<OfertaItem>();
const columns = [
  col.accessor('producto', { header: 'Producto' }),
  col.accessor('marca', { header: 'Marca' }),
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('precio_promo', { header: 'Precio promo', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('fecha_precio', { header: 'Fecha', cell: (info) => <PriceDate value={info.getValue()} /> }),
  col.accessor('leyenda_promo', {
    header: 'Condiciones',
    cell: (info) => <PromotionDetails tieneDescuento leyendaPromo={info.getValue()} compact />,
  }),
  col.accessor('descuento_pct', { header: 'Descuento %', cell: (info) => formatPercent(info.getValue()) }),
];

export function Ofertas() {
  const { provincia, localidad, cadenas } = useFilters();

  const { data, isLoading } = useQuery({
    queryKey: ['ofertas', provincia, localidad, cadenas],
    queryFn: () =>
      fetchOfertas({
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
      }),
  });

  const items = data?.items ?? [];
  const chartData = items.slice(0, 20).map((i) => ({
    label: `${i.producto.slice(0, 35)} — ${i.cadena.slice(0, 15)}`,
    value: i.descuento_pct,
  }));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Ofertas del dia</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        <FilterPanel showLocality />

        <div className="space-y-4">
          {data?.fecha && (
            <p className="text-sm text-slate-500">
              Promociones observadas el {new Date(data.fecha).toLocaleDateString('es-AR')}
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {items.length > 0 && (
            <>
              <div className="grid grid-cols-3 gap-4">
                <KpiCard label="Productos en oferta" value={String(items.length)} />
                <KpiCard
                  label="Descuento promedio"
                  value={formatPercent(items.reduce((s, i) => s + i.descuento_pct, 0) / items.length)}
                />
                <KpiCard
                  label="Mayor descuento"
                  value={formatPercent(Math.max(...items.map((i) => i.descuento_pct)))}
                />
              </div>

              <h2 className="text-lg font-semibold text-slate-800">Mejores ofertas</h2>
              <DataTable data={items} columns={columns} />

              <h2 className="text-lg font-semibold text-slate-800">Top descuentos</h2>
              <VariationBarChart data={chartData} colorMode="green" />
            </>
          )}

          {!isLoading && items.length === 0 && data && (
            <p className="text-sm text-slate-400">No hay promociones activas para los filtros seleccionados.</p>
          )}
        </div>
      </div>
    </div>
  );
}
