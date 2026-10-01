import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import { fetchLocales, fetchStorePromos, geocode, type NearbyStore, type StorePromo } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { DataTable } from '../components/tables/DataTable';
import { StoreLocatorMap } from '../components/maps/StoreLocator';
import { formatCurrency, formatPercent } from '../lib/constants';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';

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
      <PromotionDetails tieneDescuento leyendaPromo={info.getValue()} compact />
    ),
  }),
  promoCol.accessor('descuento_pct', { header: 'Descuento %', cell: (info) => formatPercent(info.getValue()) }),
];

function StoreExpander({ store }: { store: NearbyStore }) {
  const [isOpen, setIsOpen] = useState(false);
  const { data: promos = [] } = useQuery({
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
          {promos.length > 0 ? (
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
  const { cadenas } = useFilters();
  const [mode, setMode] = useState<'address' | 'coords'>('address');
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [manualLat, setManualLat] = useState(-34.6037);
  const [manualLon, setManualLon] = useState(-58.3816);
  const [radius, setRadius] = useState(5);
  const [searchTrigger, setSearchTrigger] = useState(0);

  // Geocoding
  const { data: geoResult, isFetching: isGeocoding } = useQuery({
    queryKey: ['geocode', address, city, searchTrigger],
    queryFn: () => geocode(address, city),
    enabled: mode === 'address' && address.length > 3 && searchTrigger > 0,
  });

  const lat = mode === 'address' ? geoResult?.lat ?? null : manualLat;
  const lon = mode === 'address' ? geoResult?.lon ?? null : manualLon;

  const { data: stores = [], isLoading } = useQuery({
    queryKey: ['locales', lat, lon, radius, cadenas],
    queryFn: () =>
      fetchLocales({
        lat: lat!,
        lon: lon!,
        radius,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
      }),
    enabled: lat != null && lon != null,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Locales Cercanos</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        <div className="space-y-4">
          <FilterPanel />

          <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
            <h3 className="text-sm font-semibold text-slate-700">Tu ubicacion</h3>

            <div className="flex gap-2">
              {(['address', 'coords'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                    mode === m
                      ? 'bg-slate-900 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {m === 'address' ? 'Direccion' : 'Coordenadas'}
                </button>
              ))}
            </div>

            {mode === 'address' ? (
              <div className="space-y-2">
                <input
                  type="text"
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  placeholder="Av. Corrientes 1234"
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
                <input
                  type="text"
                  value={city}
                  onChange={(e) => setCity(e.target.value)}
                  placeholder="Ciudad"
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  onClick={() => setSearchTrigger((n) => n + 1)}
                  disabled={address.length < 3}
                  className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {isGeocoding ? 'Buscando...' : 'Buscar'}
                </button>
                {geoResult?.lat && (
                  <p className="text-xs text-slate-500">
                    Ubicacion: {geoResult.lat.toFixed(4)}, {geoResult.lon!.toFixed(4)}
                  </p>
                )}
                {geoResult && !geoResult.lat && (
                  <p className="text-xs text-red-500">No se encontro la direccion.</p>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-xs text-slate-500">Latitud</label>
                  <input
                    type="number"
                    value={manualLat}
                    onChange={(e) => setManualLat(Number(e.target.value))}
                    step={0.0001}
                    className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500">Longitud</label>
                  <input
                    type="number"
                    value={manualLon}
                    onChange={(e) => setManualLon(Number(e.target.value))}
                    step={0.0001}
                    className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  />
                </div>
              </div>
            )}

            <div>
              <label className="block text-xs text-slate-500 mb-1">Radio: {radius} km</label>
              <input
                type="range"
                min={1}
                max={50}
                value={radius}
                onChange={(e) => setRadius(Number(e.target.value))}
                className="w-full"
              />
            </div>
          </div>
        </div>

        <div className="space-y-4">
          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {lat != null && lon != null && stores.length > 0 && (
            <>
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                <p className="text-sm text-emerald-800">
                  <strong>{stores.length} locales</strong> encontrados en un radio de {radius} km
                </p>
              </div>

              <StoreLocatorMap
                userLat={lat}
                userLon={lon}
                radiusKm={radius}
                stores={stores}
              />

              <h2 className="text-lg font-semibold text-slate-800">Listado de locales</h2>
              <DataTable data={stores} columns={storeColumns} />

              <h2 className="text-lg font-semibold text-slate-800">Detalle por local</h2>
              <p className="text-sm text-slate-500">Selecciona un local para ver productos en oferta</p>
              <div className="space-y-2">
                {stores.slice(0, 20).map((store) => (
                  <StoreExpander key={store.sucursal_sk} store={store} />
                ))}
              </div>
            </>
          )}

          {lat != null && lon != null && !isLoading && stores.length === 0 && (
            <p className="text-sm text-slate-400">
              No se encontraron locales en un radio de {radius} km.
            </p>
          )}

          {lat == null && mode === 'coords' && null}
          {lat == null && mode === 'address' && searchTrigger === 0 && (
            <p className="text-sm text-slate-400">Ingresa una direccion para buscar locales cercanos.</p>
          )}
        </div>
      </div>
    </div>
  );
}
