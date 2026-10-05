import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchOfertas, type OfertaItem } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { useAddressLocation } from '../hooks/useAddressLocation';
import { FilterPanel } from '../components/filters/FilterPanel';
import { LocationFilter } from '../components/filters/LocationFilter';
import { RadiusNotice } from '../components/filters/RadiusNotice';
import { DataTable } from '../components/tables/DataTable';
import { VariationBarChart } from '../components/charts/VariationBarChart';
import { KpiCard } from '../components/layout/KpiCard';
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
  const { provincia, localidad, cadenas } = useFilters();
  const location = useAddressLocation(2);
  // Every offer in the country takes about 15 s, so it waits for a location,
  // a province or an explicit request.
  const [everywhere, setEverywhere] = useState(false);
  const chosen = location.located || !!provincia || everywhere;

  // Largest discount first, a page at a time; the summary covers every offer.
  const { data: pages, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['ofertas', provincia, localidad, cadenas, location.lat, location.lon, location.radius],
    queryFn: ({ pageParam }) =>
      fetchOfertas({
        provincia: provincia || undefined,
        localidad: localidad || undefined,
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

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        <div className="space-y-4">
          <LocationFilter location={location} title="Ofertas cerca de" />
          <FilterPanel showLocality />
        </div>

        <div className="space-y-4">
          {!chosen && (
            <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-600 space-y-2">
              <p>Ingresa una direccion para ver las ofertas de los locales cercanos, o elegi una provincia.</p>
              <button onClick={() => setEverywhere(true)} className="text-blue-700 hover:underline">
                Ver las ofertas de todo el pais (puede tardar)
              </button>
            </div>
          )}

          {location.located && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              Ofertas de locales a menos de {location.radius} km de tu ubicacion
              <RadiusNotice area={location.area} />
            </div>
          )}

          {data?.fecha && (
            <p className="text-sm text-slate-500">
              Promociones observadas el {new Date(data.fecha).toLocaleDateString('es-AR')}
            </p>
          )}

          {chosen && isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {items.length > 0 && (
            <>
              <div className="grid grid-cols-3 gap-4">
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
              </div>

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
