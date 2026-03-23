import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchBuscador, fetchBuscadorDates, type Product, type StorePrice } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { DataTable } from '../components/tables/DataTable';
import { PriceMap } from '../components/maps/PriceMap';
import { KpiCard } from '../components/layout/KpiCard';
import { formatCurrency } from '../lib/constants';

const col = createColumnHelper<StorePrice>();
const columns = [
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('tipo', { header: 'Tipo' }),
  col.accessor('localidad', { header: 'Localidad' }),
  col.accessor('provincia', { header: 'Provincia' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('mejor_precio', { header: 'Mejor precio', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('ahorro', { header: 'Ahorro', cell: (info) => formatCurrency(info.getValue()) }),
];

export function Buscador() {
  const { provincia, localidad, cadenas } = useFilters();
  const [product, setProduct] = useState<Product | null>(null);
  const [fechaId, setFechaId] = useState<number | null>(null);
  const [showMap, setShowMap] = useState(false);

  const { data: dates = [] } = useQuery({
    queryKey: ['buscador-dates', product?.producto_sk],
    queryFn: () => fetchBuscadorDates(product!.producto_sk),
    enabled: !!product,
  });

  // Auto-select first date
  const selectedFechaId = fechaId ?? (dates.length > 0 ? dates[0].fecha_id : null);

  const { data: stores = [], isLoading } = useQuery({
    queryKey: ['buscador', product?.producto_sk, selectedFechaId, provincia, localidad, cadenas],
    queryFn: () =>
      fetchBuscador({
        producto_sk: product!.producto_sk,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
      }),
    enabled: !!product && !!selectedFechaId,
  });

  const cheapest = stores.length > 0 ? stores[0] : null;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Buscador de Precios</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        {/* Filters sidebar */}
        <div className="space-y-4">
          <FilterPanel showLocality />
          <ProductSearch onSelect={setProduct} selected={product} />

          {dates.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Fecha</label>
              <select
                value={selectedFechaId ?? ''}
                onChange={(e) => setFechaId(Number(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {dates.map((d) => (
                  <option key={d.fecha_id} value={d.fecha_id}>
                    {d.fecha}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Results */}
        <div className="space-y-4">
          {!product && (
            <p className="text-sm text-slate-400">Busca un producto para comenzar.</p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {cheapest && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
              <p className="text-sm text-emerald-800">
                <strong>Mejor precio:</strong> {cheapest.cadena} — {cheapest.sucursal} ({cheapest.localidad})
              </p>
              <p className="text-lg font-bold text-emerald-900">
                {formatCurrency(cheapest.mejor_precio)}
                {cheapest.ahorro > 0 && (
                  <span className="ml-2 text-sm font-normal">
                    (ahorro {formatCurrency(cheapest.ahorro)} vs lista)
                  </span>
                )}
              </p>
            </div>
          )}

          {stores.length > 0 && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Locales" value={String(stores.length)} />
                <KpiCard label="Precio min." value={formatCurrency(Math.min(...stores.map((s) => s.mejor_precio)))} />
                <KpiCard label="Precio max." value={formatCurrency(Math.max(...stores.map((s) => s.mejor_precio)))} />
                <KpiCard
                  label="Precio prom."
                  value={formatCurrency(stores.reduce((sum, s) => sum + s.mejor_precio, 0) / stores.length)}
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-lg font-semibold text-slate-800">Precios por local</h2>
                  <label className="flex items-center gap-2 text-sm text-slate-600">
                    <input
                      type="checkbox"
                      checked={showMap}
                      onChange={(e) => setShowMap(e.target.checked)}
                      className="rounded"
                    />
                    Ver mapa
                  </label>
                </div>

                {showMap && <PriceMap stores={stores} />}

                <DataTable data={stores} columns={columns} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
