import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import {
  fetchBuscador,
  fetchBuscadorDates,
  geocode,
  searchProducts,
  type Product,
  type StorePrice,
} from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { DataTable } from '../components/tables/DataTable';
import { PriceMap } from '../components/maps/PriceMap';
import { KpiCard } from '../components/layout/KpiCard';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';
import { formatCurrency } from '../lib/constants';

// ── Single-product table columns ──
const col = createColumnHelper<StorePrice>();
const columns = [
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('tipo', { header: 'Tipo' }),
  col.accessor('localidad', { header: 'Localidad' }),
  col.accessor('provincia', { header: 'Provincia' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('mejor_precio', { header: 'Mejor precio', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('fecha_precio', { header: 'Fecha', cell: (info) => <PriceDate value={info.getValue()} /> }),
  col.accessor('ahorro', { header: 'Ahorro', cell: (info) => formatCurrency(info.getValue()) }),
  col.display({
    id: 'promocion',
    header: 'Condiciones',
    cell: (info) => (
      <PromotionDetails
        tieneDescuento={info.row.original.tiene_descuento}
        leyendaPromo={info.row.original.leyenda_promo}
        precioCondicional={info.row.original.precio_condicional}
        leyendaCondicional={info.row.original.leyenda_condicional}
        condicionAparte={info.row.original.condicion}
        compact
      />
    ),
  }),
];

export function Buscador() {
  const { provincia, localidad, cadenas } = useFilters();

  // Two mutually exclusive modes: one product, or the cards of a search
  const [product, setProduct] = useState<Product | null>(null);
  const [genericQuery, setGenericQuery] = useState<string | null>(null);

  const [fechaId, setFechaId] = useState<number | null>(null);
  const [showMap, setShowMap] = useState(false);

  // ── Location filter ──
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [radius, setRadius] = useState(1);
  const [geoTrigger, setGeoTrigger] = useState(0);
  const [locationEnabled, setLocationEnabled] = useState(false);

  const { data: geoResult, isFetching: isGeocoding } = useQuery({
    queryKey: ['geocode', address, city, geoTrigger],
    queryFn: () => geocode(address, city),
    enabled: address.length > 3 && geoTrigger > 0,
  });

  const userLat = locationEnabled && geoResult?.lat ? geoResult.lat : null;
  const userLon = locationEnabled && geoResult?.lon ? geoResult.lon : null;

  const handleGeoSearch = () => {
    setLocationEnabled(true);
    setGeoTrigger((n) => n + 1);
  };

  const clearLocation = () => {
    setLocationEnabled(false);
    setAddress('');
    setGeoTrigger(0);
  };

  const handleSelectProduct = (p: Product | null) => {
    setProduct(p);
    setGenericQuery(null);
    setFechaId(null);
  };

  const handleSearchAll = (q: string) => {
    setGenericQuery(q || null);
    setProduct(null);
    setFechaId(null);
  };

  // ── Dates ──
  const { data: dates = [] } = useQuery({
    queryKey: ['buscador-dates', product?.producto_sk],
    queryFn: () => fetchBuscadorDates(product!.producto_sk),
    enabled: !!product,
  });

  const activeDates = product ? dates : [];
  const selectedFechaId = fechaId ?? (activeDates.length > 0 ? activeDates[0].fecha_id : null);

  // ── Location params for API ──
  const locationParams = userLat != null && userLon != null
    ? { lat: userLat, lon: userLon, radius } : {};

  // ── Single-product results ──
  const { data: stores = [], isLoading: isLoadingSingle } = useQuery({
    queryKey: ['buscador', product?.producto_sk, selectedFechaId, provincia, localidad, cadenas, userLat, userLon, radius],
    queryFn: () =>
      fetchBuscador({
        producto_sk: product!.producto_sk,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
      }),
    enabled: !!product && !!selectedFechaId,
  });

  // ── Product cards of a search (latest published day) ──
  const { data: cards = [], isLoading: isLoadingCards } = useQuery({
    queryKey: ['product-cards', genericQuery, userLat, userLon, radius],
    queryFn: () => searchProducts({ q: genericQuery!, ...locationParams }),
    enabled: !!genericQuery,
  });

  const isLoading = isLoadingSingle || isLoadingCards;
  const isGenericMode = !!genericQuery;
  const nearby = userLat != null && userLon != null;
  const cheapest = !isGenericMode && stores.length > 0 ? stores[0] : null;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Buscador de Precios</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        {/* Filters sidebar */}
        <div className="space-y-4">
          <FilterPanel showLocality />
          <ProductSearch
            onSelect={handleSelectProduct}
            onSearchAll={handleSearchAll}
            selected={product}
            genericQuery={genericQuery}
          />

          {activeDates.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Fecha</label>
              <select
                value={selectedFechaId ?? ''}
                onChange={(e) => setFechaId(Number(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {activeDates.map((d) => (
                  <option key={d.fecha_id} value={d.fecha_id}>
                    {d.fecha}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Location filter */}
          <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-700">Filtrar por ubicacion</h3>
              {locationEnabled && (
                <button onClick={clearLocation} className="text-xs text-slate-400 hover:text-slate-600">
                  Desactivar
                </button>
              )}
            </div>
            <input
              type="text"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && address.length > 3) handleGeoSearch(); }}
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
              onClick={handleGeoSearch}
              disabled={address.length < 3}
              className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {isGeocoding ? 'Buscando...' : 'Buscar ubicacion'}
            </button>
            {locationEnabled && geoResult?.lat && (
              <>
                <p className="text-xs text-slate-500">
                  Ubicacion: {geoResult.lat.toFixed(4)}, {geoResult.lon!.toFixed(4)}
                </p>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Radio: {radius} km</label>
                  <input
                    type="range"
                    min={0.5}
                    max={10}
                    step={0.5}
                    value={radius}
                    onChange={(e) => setRadius(Number(e.target.value))}
                    className="w-full"
                  />
                </div>
              </>
            )}
            {locationEnabled && geoResult && !geoResult.lat && (
              <p className="text-xs text-red-500">No se encontro la direccion.</p>
            )}
          </div>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {!product && !genericQuery && (
            <p className="text-sm text-slate-400">
              Busca un producto para ver precios, o presiona Enter para buscar todos los resultados.
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {/* Location active indicator */}
          {locationEnabled && userLat != null && (product || genericQuery) && !isLoading && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              Mostrando resultados a menos de {radius} km de tu ubicacion
              {((isGenericMode && cards.length > 0 && cards.every((c) => c.sucursales === 0))
                || (!isGenericMode && stores.length === 0 && selectedFechaId)) && (
                <span> — no hay locales cerca. Intenta aumentar el radio.</span>
              )}
            </div>
          )}

          {/* ── Single-product results ── */}
          {!isGenericMode && cheapest && (
            <>
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
                <PromotionDetails
                  tieneDescuento={cheapest.tiene_descuento}
                  leyendaPromo={cheapest.leyenda_promo}
                  precioCondicional={cheapest.precio_condicional}
                  leyendaCondicional={cheapest.leyenda_condicional}
                  condicionAparte={cheapest.condicion}
                />
                <PriceDate value={cheapest.fecha_precio} />
              </div>

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

          {/* ── Product cards ── */}
          {isGenericMode && cards.length > 0 && (
            <div className="grid sm:grid-cols-2 gap-3">
              {cards.map((card) => (
                <button
                  key={card.producto_sk}
                  onClick={() => handleSelectProduct(card)}
                  className="rounded-lg border border-slate-200 bg-white p-4 text-left hover:border-blue-300 hover:bg-blue-50/40 transition-colors"
                >
                  <p className="font-medium text-slate-900">{card.nombre}</p>
                  <p className="text-xs text-slate-400">
                    {card.nombre !== card.descripcion && `${card.descripcion} · `}{card.id_producto}
                  </p>
                  {card.precio_desde != null && card.fecha_precio_desde ? (
                    <>
                      <p className="mt-2 text-sm text-slate-600">
                        desde{' '}
                        <span className="text-lg font-bold text-emerald-700">
                          {formatCurrency(card.precio_desde)}
                        </span>
                      </p>
                      <p className="text-xs text-slate-500">
                        en {card.sucursales} {card.sucursales === 1 ? 'local' : 'locales'}
                        {nearby && ' cerca'}
                      </p>
                      <PriceDate value={card.fecha_precio_desde} />
                    </>
                  ) : (
                    <p className="mt-2 text-sm text-slate-400">Ningún local cerca lo tiene.</p>
                  )}
                </button>
              ))}
            </div>
          )}

          {isGenericMode && !isLoading && cards.length === 0 && (
            <p className="text-sm text-slate-400">
              No se encontraron productos para "{genericQuery}".
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
