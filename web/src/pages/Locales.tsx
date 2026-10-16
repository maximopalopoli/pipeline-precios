import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchLocales, fetchStorePromos, type NearbyStore, type StorePromo } from '../lib/api';
import { useAddressLocation } from '../hooks/useAddressLocation';
import { LocationBar } from '../components/filters/LocationBar';
import { ChainChips } from '../components/filters/ChainChips';
import { DataTable } from '../components/tables/DataTable';
import { QueryError } from '../components/layout/QueryError';
import { StoreLocatorMap } from '../components/maps/StoreLocator';
import { formatCurrency, formatPercent } from '../lib/constants';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';

const STORES_PER_PAGE = 100;

const storeCol = createColumnHelper<NearbyStore>();
const storeColumns = [
  storeCol.accessor('cadena', { header: 'Cadena' }),
  storeCol.accessor('nombre', { header: 'Sucursal' }),
  storeCol.accessor('tipo', { header: 'Tipo' }),
  storeCol.accessor('localidad', { header: 'Localidad' }),
  storeCol.accessor('distancia_km', { header: 'Distancia (km)' }),
];

const promoCol = createColumnHelper<StorePromo>();
const promoColumns = [
  promoCol.accessor('producto', { header: 'Producto' }),
  promoCol.accessor('marca', { header: 'Marca' }),
  promoCol.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  promoCol.accessor('precio_promo', { header: 'Precio promo', cell: (info) => formatCurrency(info.getValue()) }),
  promoCol.accessor('fecha_precio', { header: 'Fecha', cell: (info) => <PriceDate value={info.getValue()} /> }),
  promoCol.accessor('leyenda_promo', {
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
  promoCol.accessor('descuento_pct', { header: 'Descuento %', cell: (info) => formatPercent(info.getValue()) }),
];

function StoreExpander({ store }: { store: NearbyStore }) {
  const [isOpen, setIsOpen] = useState(false);
  const { data: promos = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['store-promos', store.sucursal_sk],
    queryFn: () => fetchStorePromos(store.sucursal_sk),
    enabled: isOpen,
  });

  const direccion = `${store.calle || ''} ${store.numero || ''}`.trim();

  return (
    <div className="border border-slate-200 rounded-md">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm text-left hover:bg-slate-50"
      >
        <span>
          <strong>{store.cadena}</strong> — {store.nombre} ({direccion}, {store.localidad}) — {store.distancia_km} km
        </span>
        <span className="text-slate-400">{isOpen ? '▲' : '▼'}</span>
      </button>
      {isOpen && (
        <div className="px-4 pb-3">
          {isLoading ? (
            <p className="text-sm text-slate-400">Cargando...</p>
          ) : isError ? (
            <QueryError compact what="las promociones del local" error={error} onRetry={() => refetch()} />
          ) : promos.length > 0 ? (
            <DataTable data={promos} columns={promoColumns} />
          ) : (
            <p className="text-sm text-slate-400">Sin promociones activas en este local.</p>
          )}
        </div>
      )}
    </div>
  );
}

export function Locales() {
  const [cadenas, setCadenas] = useState<string[]>([]);
  const location = useAddressLocation(5);
  const { lat, lon } = location;
  const searchRadius = location.radius;

  const {
    data: storePages,
    isLoading,
    isError,
    error,
    refetch,
    fetchNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useInfiniteQuery({
    queryKey: ['locales', lat, lon, searchRadius, cadenas],
    queryFn: ({ pageParam }) =>
      fetchLocales({
        lat: lat!,
        lon: lon!,
        radius: searchRadius,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        limit: STORES_PER_PAGE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.hay_mas ? pages.length * STORES_PER_PAGE : undefined),
    enabled: location.located && location.ready,
  });
  const stores = storePages?.pages.flatMap((page) => page.sucursales) ?? [];
  const total = storePages?.pages[0]?.total ?? 0;
  const moreStores = storePages?.pages.at(-1)?.hay_mas ?? false;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Locales Cercanos</h1>

      <LocationBar location={location} />
      <ChainChips location={location} chosen={cadenas} onChange={setCadenas} />

      <div className="space-y-4">
        {!location.located && (
          <p className="text-sm text-slate-400">Indicá dónde estás para buscar locales cercanos.</p>
        )}

        {location.located && isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

        {isError && !isFetchNextPageError && (
          <QueryError what="los locales" error={error} onRetry={() => refetch()} />
        )}

        {lat != null && lon != null && stores.length > 0 && (
          <>
            <p className="text-sm text-slate-600">
              {moreStores ? (
                <>
                  Los <strong>{stores.length} más cercanos</strong> de {total} locales a menos de {searchRadius} km.
                  El mapa muestra solo los cargados.
                </>
              ) : (
                <>
                  <strong>{total} {total === 1 ? 'local' : 'locales'}</strong> a menos de {searchRadius} km
                </>
              )}
            </p>

            <StoreLocatorMap
              userLat={lat}
              userLon={lon}
              radiusKm={searchRadius}
              stores={stores}
            />

            <h2 className="text-lg font-semibold text-slate-800">Listado de locales</h2>
            <DataTable data={stores} columns={storeColumns} />
            {moreStores && !isFetchNextPageError && (
              <button
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              >
                {isFetchingNextPage ? 'Cargando...' : 'Ver más locales'}
              </button>
            )}
            {isFetchNextPageError && (
              <QueryError compact what="más locales" error={error} onRetry={() => fetchNextPage()} />
            )}

            <h2 className="text-lg font-semibold text-slate-800">Detalle por local</h2>
            <p className="text-sm text-slate-500">Elegí un local para ver sus productos en oferta</p>
            <div className="space-y-2">
              {stores.slice(0, 20).map((store) => (
                <StoreExpander key={store.sucursal_sk} store={store} />
              ))}
            </div>
          </>
        )}

        {location.located && !isLoading && !isError && stores.length === 0 && (
          <p className="text-sm text-slate-400">
            No se encontraron locales en un radio de {searchRadius} km.
          </p>
        )}
      </div>
    </div>
  );
}
