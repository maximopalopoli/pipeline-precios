import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchOfertas, type OfertaItem } from '../lib/api';
import { useAddressLocation } from '../hooks/useAddressLocation';
import { LocationBar } from '../components/filters/LocationBar';
import { ChainChips } from '../components/filters/ChainChips';
import { DataTable } from '../components/tables/DataTable';
import { VariationBarChart } from '../components/charts/VariationBarChart';
import { KpiCard } from '../components/layout/KpiCard';
import { QueryError } from '../components/layout/QueryError';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';
import { formatCurrency, formatPercent } from '../lib/constants';

const col = createColumnHelper<OfertaItem>();
const columns = [
  col.accessor('producto', {
    header: 'Producto',
    cell: (info) => (
      <>
        <span>{info.getValue()}</span>
        {info.getValue() !== info.row.original.descripcion && (
          <span className="block text-xs text-slate-400">{info.row.original.descripcion}</span>
        )}
      </>
    ),
  }),
  col.accessor('marca', { header: 'Marca' }),
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('precio_promo', { header: 'Precio promo', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('fecha_precio', { header: 'Fecha', cell: (info) => <PriceDate value={info.getValue()} /> }),
  col.accessor('leyenda_promo', {
    header: 'Condiciones',
    cell: (info) => (
      <PromotionDetails
        tieneDescuento
        leyendaPromo={info.getValue()}
        condicion={info.row.original.condicion}
        compact
      />
    ),
  }),
  col.accessor('descuento_pct', { header: 'Descuento %', cell: (info) => formatPercent(info.getValue()) }),
  col.accessor('sucursales', { header: 'Locales' }),
];

const OFFERS_PER_PAGE = 50;

export function Ofertas() {
  const [cadenas, setCadenas] = useState<string[]>([]);
  const location = useAddressLocation(2);
  // Every offer in the published area takes about 15 s, so it waits for a
  // location or an explicit request.
  const [everywhere, setEverywhere] = useState(false);
  const chosen = location.located || everywhere;

  // Largest discount first, a page at a time; the summary covers every offer.
  const {
    data: pages,
    isLoading,
    isError,
    error,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useInfiniteQuery({
    queryKey: ['ofertas', cadenas, location.lat, location.lon, location.radius],
    queryFn: ({ pageParam }) =>
      fetchOfertas({
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...location.params,
        limit: OFFERS_PER_PAGE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => (last.hay_mas ? all.length * OFFERS_PER_PAGE : undefined),
    enabled: chosen && location.ready,
  });

  const data = pages?.pages[0];
  const items = pages?.pages.flatMap((page) => page.items) ?? [];
  const chartData = items.slice(0, 20).map((i) => ({
    label: `${i.producto.slice(0, 35)} — ${i.cadena.slice(0, 15)}`,
    value: i.descuento_pct,
  }));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Ofertas del dia</h1>

      <LocationBar location={location} />
      <ChainChips location={location} chosen={cadenas} onChange={setCadenas} />

      <div className="space-y-4">
        {!chosen && (
          <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-600">
            Indicá dónde estás para ver las ofertas de los locales cercanos, o{' '}
            <button onClick={() => setEverywhere(true)} className="text-blue-700 hover:underline">
              mirá todas las ofertas (puede tardar)
            </button>
            .
          </div>
        )}

        {data?.fecha && (
          <p className="text-sm text-slate-500">
            Promociones observadas el {new Date(data.fecha).toLocaleDateString('es-AR')}
          </p>
        )}

        {chosen && isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

        {isError && !pages && (
          <QueryError what="las ofertas" error={error} onRetry={() => refetch()} />
        )}

        {items.length > 0 && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <KpiCard label="Ofertas" value={String(data?.total ?? items.length)} />
              <KpiCard
                label="Descuento promedio"
                value={data?.descuento_promedio != null ? formatPercent(data.descuento_promedio) : '—'}
              />
              <KpiCard
                label="Mayor descuento"
                value={data?.descuento_maximo != null ? formatPercent(data.descuento_maximo) : '—'}
              />
            </div>

            <h2 className="text-lg font-semibold text-slate-800">Mejores ofertas</h2>
            <DataTable data={items} columns={columns} />
            <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
              <span>
                {items.length === data?.total
                  ? `${items.length} ${items.length === 1 ? 'oferta' : 'ofertas'}`
                  : `Las ${items.length} de mayor descuento, de ${data?.total} ofertas`}
              </span>
              {hasNextPage && (
                <button
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {isFetchingNextPage ? 'Cargando...' : 'Ver más ofertas'}
                </button>
              )}
              {isFetchNextPageError && (
                <QueryError compact what="más ofertas" error={error} onRetry={() => fetchNextPage()} />
              )}
            </div>

            <h2 className="text-lg font-semibold text-slate-800">Top descuentos</h2>
            <VariationBarChart data={chartData} colorMode="green" />
          </>
        )}

        {data && !data.fecha && (
          <p className="text-sm text-slate-400">Todavía no hay precios publicados.</p>
        )}
        {data?.fecha && items.length === 0 && (
          <p className="text-sm text-slate-400">
            {location.located
              ? `No hay promociones en locales a menos de ${location.radius} km. Probá con un radio mayor.`
              : 'No hay promociones para los filtros elegidos.'}
          </p>
        )}
      </div>
    </div>
  );
}
